"""`ccst pdata migrate-uuid`: converts one project's format 3 (integer id) database to format 4
(UUID ids), identically on every machine that shares the project (design D2, D4, D6, D6c, D9).

Two paths, chosen by whether a format 4 dump is already published for the project:

- REWRITE (the first machine): every integer id becomes UUIDv5 over `"<project>:<integer id>"`
  (ids.deterministic_record_id), in `records`, every `ext_<group>` table and
  `pdata_verify_watermark`, live and soft-deleted rows alike, with every other column unchanged.
  This is only safe from a state converged with the published format 3 dump (D4): then every
  machine holds the same integer-to-record map and derives the same UUIDs. The format 3 vector is
  stored in `pdata_migration_origin` and the format 4 dump is published.
- ADOPT (each later machine): the database must be the format 3 state the published dump was
  migrated from (its `migrated_from_format3_vector` header equals this database's vector), and
  the dump must hold every record this database holds under its mapped id. The database is then
  replaced by the dump's data.

A dry run computes the same plan and preconditions and changes nothing. Every write path backs
up the database first; any failure after the backup leaves the original database in place.
"""
from __future__ import annotations

import enum
import json
import logging
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from cc_session_tools.lib import db, machine_identity, occupancy
from cc_session_tools.lib.pdata import (
    dump,
    formats,
    ids,
    rehydrate,
    repository,
    store,
    sync_check,
    vector_clock_store,
    verify,
)

logger = logging.getLogger(__name__)

BACKUP_DIR_NAME: Final = "migration-backups"
_WATERMARK_TABLE: Final = "pdata_verify_watermark"
_MAP_FUNCTION: Final = "pdata_uuid_map"
# Extension and content field names that suggest the value is another record's id, which the
# migration cannot see or rewrite (design, Risks).
_ID_LIKE_FIELD_RE: Final = re.compile(r"(?:.*_)?id|ref")


class Outcome(enum.Enum):
    NOTHING_TO_MIGRATE = "nothing_to_migrate"  # the project has no database
    ALREADY_MIGRATED = "already_migrated"      # format 4, marker recorded, nothing pending
    PLANNED = "planned"                        # dry run with every precondition holding
    REFUSED = "refused"                        # a precondition does not hold; nothing changed
    MIGRATED = "migrated"                      # rewritten, verified, marked and published
    ADOPTED = "adopted"                        # replaced by the published format 4 dump
    RESUMED = "resumed"                        # finished the steps an earlier run left undone
    FAILED = "failed"                          # an error after the backup; original in place


class MigrationPath(enum.Enum):
    REWRITE = "rewrite"
    ADOPT = "adopt"
    RESUME = "resume"


@dataclass(frozen=True, slots=True)
class Precondition:
    name: str
    holds: bool
    # The remedy when the precondition does not hold; empty when it does.
    remedy: str = ""


@dataclass(frozen=True, slots=True)
class GroupCount:
    record_group: str
    rows: int     # including soft-deleted rows
    deleted: int  # soft-deleted rows


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    project: str
    db_path: Path
    path: MigrationPath
    group_counts: tuple[GroupCount, ...]
    extension_tables: tuple[tuple[str, int], ...]
    watermark_rows: int
    format3_vector: dict[str, int]
    preconditions: tuple[Precondition, ...]
    suspicious_fields: tuple[str, ...]
    # RESUME only: the steps still to do.
    pending_steps: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return all(p.holds for p in self.preconditions)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    project: str
    outcome: Outcome
    plan: MigrationPlan | None = None
    messages: tuple[str, ...] = ()
    backup_path: Path | None = None


class MigrationFailedError(Exception):
    """A check after the rewrite or adoption did not hold."""


_STEP_VERIFY: Final = "run verify"
_STEP_MARKER: Final = f"record the {formats.UUID_IDS_MIGRATION} marker"
_STEP_PUBLISH: Final = "publish the format 4 dump"


def migrate_project(project: str, *, write: bool) -> MigrationResult:
    """Plan and, with `write`, perform the migration of `project`. Raises ValueError for an
    invalid project name and formats.UnknownPdataFormatError for a database of no known
    format; every other outcome is reported in the result."""
    db_path = store.db_path(project)
    if not db_path.exists():
        return MigrationResult(
            project=project, outcome=Outcome.NOTHING_TO_MIGRATE,
            messages=(f"no database at {db_path} - nothing to migrate",),
        )
    project_root = store.project_root(project)
    if formats.database_format(db_path) == formats.CURRENT_PDATA_FORMAT:
        return _resume(project, db_path=db_path, project_root=project_root, write=write)

    published = dump.read_latest(project_root)
    plan = _plan(project, db_path=db_path, project_root=project_root, published=published)
    if not plan.ready:
        return MigrationResult(project=project, outcome=Outcome.REFUSED, plan=plan)
    if not write:
        return MigrationResult(project=project, outcome=Outcome.PLANNED, plan=plan)
    if plan.path is MigrationPath.ADOPT:
        return _adopt(plan, project_root=project_root)
    return _rewrite(plan, project_root=project_root)


# ------------------------------------------------------------------ planning


def _plan(
    project: str, *, db_path: Path, project_root: Path, published: dump.DumpInfo,
) -> MigrationPlan:
    conn = db.connect(db_path, readonly=True)
    try:
        group_counts = _group_counts(conn)
        extension_tables = tuple(
            (table, _count(conn, table)) for table in _extension_tables(conn)
        )
        watermark_rows = _count(conn, _WATERMARK_TABLE) if _table_exists(
            conn, _WATERMARK_TABLE
        ) else 0
        vector = vector_clock_store.read_vector(conn)
        suspicious = _suspicious_fields(conn)
        old_ids = [int(r[0]) for r in conn.execute("SELECT id FROM records")]
    finally:
        conn.close()

    preconditions = [Precondition("database is format 3", True)]
    if published.present:
        path = MigrationPath.ADOPT
        preconditions.extend(_adopt_preconditions(
            project, project_root=project_root, published=published, vector=vector,
            old_ids=old_ids,
        ))
    else:
        path = MigrationPath.REWRITE
        preconditions.append(_converged_precondition(project, project_root=project_root))
    preconditions.extend(_quiet_preconditions(db_path, project_root=project_root))
    return MigrationPlan(
        project=project, db_path=db_path, path=path, group_counts=group_counts,
        extension_tables=extension_tables, watermark_rows=watermark_rows,
        format3_vector=vector, preconditions=tuple(preconditions),
        suspicious_fields=suspicious,
    )


def _reinstall_recipe(project: str) -> str:
    """The way back to a converged format 3 state once ccst 4.x is installed (design D6c)."""
    return (
        f"install {formats.LAST_FORMAT3_RELEASE} (`uv tool install "
        f"cc-session-tools=={formats.LAST_FORMAT3_RELEASE}`), run `ccst pdata sync-check "
        f"--project {project}` until it reports \"unchanged\", then upgrade again (`uv tool "
        f"install cc-session-tools --upgrade`) and re-run `{formats.MIGRATE_COMMAND} --project "
        f"{project}`"
    )


def _converged_precondition(project: str, *, project_root: Path) -> Precondition:
    name = "converged with the published format 3 dump"
    status = sync_check.format3_status(project, project_root=project_root)
    if status is sync_check.Format3Status.UNCHANGED:
        return Precondition(name, True)
    recipe = _reinstall_recipe(project)
    remedies = {
        sync_check.Format3Status.UNPUBLISHED_CHANGES: (
            "this database has unpublished changes, and ccst 4.x never writes a format 3 dump, "
            f"so they must be published with 3.x first: {recipe}"
        ),
        sync_check.Format3Status.NOT_PUBLISHED: (
            "this database has never been published, and ccst 4.x never writes a format 3 "
            f"dump, so publish it with 3.x first: {recipe}"
        ),
        sync_check.Format3Status.BEHIND: (
            "the published format 3 dump is ahead (behind: it has changes this database lacks); "
            f"fast-forward with 3.x first: {recipe}"
        ),
        sync_check.Format3Status.FORK: (
            "this database and the published format 3 dump have diverged (fork); the fork must "
            f"be resolved with the 3.x tooling first: install {formats.LAST_FORMAT3_RELEASE} "
            f"(`uv tool install cc-session-tools=={formats.LAST_FORMAT3_RELEASE}`), run `ccst "
            f"pdata resolve --project {project}` and resolve every record, then `ccst pdata "
            "sync-check` until it reports \"unchanged\", then upgrade again and re-run"
        ),
        sync_check.Format3Status.CHECKSUM_INVALID: (
            "the published format 3 dump fails its checksum; republish it from the machine with "
            f"good data using {formats.LAST_FORMAT3_RELEASE} (`ccst pdata dump --project "
            f"{project} --force`), then re-run"
        ),
    }
    return Precondition(name, False, remedies[status])


def _reentry_explanation(project: str) -> str:
    return (
        f"another machine already migrated {project} and published the format 4 dump, from a "
        "format 3 state this database is not in: this database has format 3 changes that "
        "lineage never saw, so it cannot be migrated to match it. Nothing was changed. To "
        "adopt the format 4 data, move this database aside (keep it to copy from), run "
        f"`{formats.MIGRATE_COMMAND} --project {project} --write` again so the project starts "
        "from the format 4 dump, then re-enter the missing changes by hand. Session-output rows "
        "need no re-entry: the reconcile job re-derives them. See "
        f"{formats.RUNBOOK_PATH} (troubleshooting: lineage mismatch)"
    )


def _adopt_preconditions(
    project: str, *, project_root: Path, published: dump.DumpInfo, vector: dict[str, int],
    old_ids: list[int],
) -> list[Precondition]:
    if not published.checksum_valid:
        return [Precondition(
            "the published format 4 dump is readable", False,
            "the format 4 dump fails its checksum; republish it from a migrated machine "
            f"(`ccst pdata dump --project {project} --force`), then re-run",
        )]
    lineage = Precondition("this database is the format 3 state the format 4 dump came from", True)
    if published.migrated_from_format3_vector != vector:
        return [Precondition(lineage.name, False, _reentry_explanation(project))]
    missing = _ids_missing_from_dump(project, project_root=project_root, old_ids=old_ids)
    if missing:
        return [lineage, Precondition(
            "the format 4 dump holds every record of this database", False,
            f"{len(missing)} record(s) of this database are not in the format 4 dump "
            f"(integer ids {', '.join(str(i) for i in missing)}). "
            f"{_reentry_explanation(project)}",
        )]
    return [lineage, Precondition("the format 4 dump holds every record of this database", True)]


def _ids_missing_from_dump(project: str, *, project_root: Path, old_ids: list[int]) -> list[int]:
    """The integer ids whose mapped UUID is not a record in the published format 4 dump.
    Records are never hard-deleted, so a converged machine's mapped ids are all in the dump."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(dump.sql_body(dump.latest_path(project_root).read_text()))
        dump_ids = {str(r[0]) for r in conn.execute("SELECT id FROM records")}
    finally:
        conn.close()
    return sorted(
        i for i in old_ids if ids.deterministic_record_id(project, str(i)) not in dump_ids
    )


def _quiet_preconditions(db_path: Path, *, project_root: Path) -> list[Precondition]:
    session = Precondition(f"no `claude` session is running in {project_root}", True)
    if occupancy.is_occupied(project_root):
        session = Precondition(
            session.name, False,
            f"close every running `claude` session in {project_root}, then re-run from a plain "
            "terminal",
        )
    lock_error = _write_lock_error(db_path)
    unlocked = Precondition("the database is not locked", True)
    if lock_error is not None:
        unlocked = Precondition(
            unlocked.name, False,
            f"another process is using {db_path} right now ({lock_error}); close running "
            "`claude` sessions, wait for scheduled pdata jobs to finish, then re-run",
        )
    return [session, unlocked]


def _write_lock_error(db_path: Path) -> str | None:
    """None when a write transaction could start on `db_path` right now, else the reason it
    could not. A throwaway BEGIN IMMEDIATE / ROLLBACK with no busy wait, the same probe as
    init_service._check_db_not_locked."""
    conn = sqlite3.connect(str(db_path), timeout=0, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=0")
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            return str(exc)
        conn.execute("ROLLBACK")
        return None
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _count(conn: sqlite3.Connection, table: str) -> int:
    count: int = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    return count


def _extension_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        str(r[0]) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ext\\_%' "
            "ESCAPE '\\' ORDER BY name"
        )
    ]


def _group_counts(conn: sqlite3.Connection) -> tuple[GroupCount, ...]:
    return tuple(
        GroupCount(str(r[0]), rows=int(r[1]), deleted=int(r[2]))
        for r in conn.execute(
            "SELECT record_group, COUNT(*), COUNT(deleted_at) FROM records "
            "GROUP BY record_group ORDER BY record_group"
        )
    )


def _suspicious_fields(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Extension columns, described fields and top-level JSON content keys whose names suggest
    another record's id, as "<table or group>: <kind> <name>" lines."""
    found: set[str] = set()
    for table in _extension_tables(conn):
        for row in conn.execute(f'PRAGMA table_info("{table}")'):
            name = str(row[1])
            if name != "record_id" and _ID_LIKE_FIELD_RE.fullmatch(name):
                found.add(f"{table}: extension field {name}")
    for group, field_name in conn.execute(
        "SELECT record_group, field_name FROM record_group_fields"
    ):
        if _ID_LIKE_FIELD_RE.fullmatch(str(field_name)):
            found.add(f"{group}: described field {field_name}")
    for group, content in conn.execute("SELECT record_group, content FROM records"):
        for key in _json_object_keys(str(content)):
            if _ID_LIKE_FIELD_RE.fullmatch(key):
                found.add(f"{group}: content key {key}")
    return tuple(sorted(found))


def _json_object_keys(content: str) -> list[str]:
    if not content.lstrip().startswith("{"):
        return []
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return []
    return [str(k) for k in parsed] if isinstance(parsed, dict) else []


# ------------------------------------------------------------------ backup and restore


def _take_backup(plan: MigrationPlan) -> Path:
    """Back up the database into migration-backups/ and check the copy is a readable format 3
    database with the planned row counts. Raises MigrationFailedError if it is not."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = plan.db_path.parent / BACKUP_DIR_NAME / (
        f"pdata-{plan.project}-pre-4.0.0-{stamp}.db"
    )
    db.backup_to(plan.db_path, backup_path)
    conn = db.connect(backup_path, readonly=True)
    try:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise MigrationFailedError(f"the backup {backup_path} fails PRAGMA quick_check")
        if formats.pdata_format(conn) != formats.PDATA_FORMAT_INTEGER_IDS:
            raise MigrationFailedError(f"the backup {backup_path} is not a format 3 database")
        if _group_counts(conn) != plan.group_counts:
            raise MigrationFailedError(
                f"the backup {backup_path} does not hold the database's row counts"
            )
    finally:
        conn.close()
    return backup_path


def _restore(backup_path: Path, db_path: Path) -> None:
    """Copy the backup back over the database, through SQLite's backup API so the live file's
    WAL is handled."""
    src = sqlite3.connect(str(backup_path))
    dst = sqlite3.connect(str(db_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _failed(
    plan: MigrationPlan, *, backup_path: Path | None, error: BaseException, restored: bool,
) -> MigrationResult:
    if restored:
        where = f"the original database was restored from the backup {backup_path}"
    elif backup_path is not None:
        where = f"the original database is unchanged; the backup is {backup_path}"
    else:
        where = "the database is unchanged"
    logger.warning("pdata migrate-uuid %s failed: %s", plan.project, error)
    return MigrationResult(
        project=plan.project, outcome=Outcome.FAILED, plan=plan, backup_path=backup_path,
        messages=(f"migration failed: {error}; {where}",),
    )


# ------------------------------------------------------------------ the rewrite (first machine)


def _rewrite(plan: MigrationPlan, *, project_root: Path) -> MigrationResult:
    try:
        backup_path = _take_backup(plan)
    except (OSError, sqlite3.Error, MigrationFailedError) as exc:
        return _failed(plan, backup_path=None, error=exc, restored=False)

    baseline = _known_fail_issues(plan.project, plan.db_path, include_row_count_parity=True)
    try:
        _convert(plan)
    except (OSError, sqlite3.Error, MigrationFailedError) as exc:
        return _failed(plan, backup_path=backup_path, error=exc, restored=False)

    try:
        _check_converted(plan)
        _verify_against(plan.project, baseline)
        _record_marker(plan.project)
        machine_id = _publish(plan.project, project_root=project_root)
    except (OSError, sqlite3.Error, MigrationFailedError) as exc:
        _restore(backup_path, plan.db_path)
        return _failed(plan, backup_path=backup_path, error=exc, restored=True)

    rows = sum(g.rows for g in plan.group_counts)
    return MigrationResult(
        project=plan.project, outcome=Outcome.MIGRATED, plan=plan, backup_path=backup_path,
        messages=(
            f"backup: {backup_path}",
            f"rewrote {rows} record(s), {len(plan.extension_tables)} extension table(s) and "
            f"{plan.watermark_rows} verify watermark row(s) to UUID ids; verify passed",
            f"recorded the {formats.UUID_IDS_MIGRATION} marker",
            f"published {dump.latest_path(project_root)} (machine_id={machine_id})",
        ),
    )


@contextmanager
def _rewrite_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = db.connect(db_path)
    conn.isolation_level = None
    try:
        yield conn
    finally:
        conn.close()


def _convert(plan: MigrationPlan) -> None:
    """The single transaction (design D9): every id table rebuilt with the uuid5 mapping and the
    format 3 vector recorded as the migration origin, foreign keys off."""
    with _rewrite_connection(plan.db_path) as conn:
        def mapped(old_id: int) -> str:
            return ids.deterministic_record_id(plan.project, str(old_id))

        conn.create_function(_MAP_FUNCTION, 1, mapped, deterministic=True)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            _rewrite_table(conn, "records", id_column="id")
            for table in _extension_tables(conn):
                _rewrite_table(conn, table, id_column="record_id")
            if _table_exists(conn, _WATERMARK_TABLE):
                _rewrite_table(conn, _WATERMARK_TABLE, id_column="record_id")
            vector_clock_store.write_migration_origin(conn, plan.format3_vector)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")
        db.checkpoint(conn)


def _rewrite_table(conn: sqlite3.Connection, table: str, *, id_column: str) -> None:
    """Rebuild `table` with `id_column` declared TEXT and every value mapped: create a copy
    from the table's own CREATE statement, insert-select, drop, rename, recreate its indexes.
    Runs inside the caller's transaction."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    create_sql = str(row[0])
    staging = f"{table}__uuid_migration"
    id_decl = re.compile(rf"(?<![\w\"]){id_column}\s+INTEGER\s+PRIMARY\s+KEY", re.IGNORECASE)
    create_sql, replaced = id_decl.subn(f"{id_column} TEXT PRIMARY KEY", create_sql)
    if replaced != 1:
        raise MigrationFailedError(
            f"{table}: expected one `{id_column} INTEGER PRIMARY KEY` column, found {replaced}"
        )
    head = re.compile(
        rf"^CREATE TABLE\s+(?:\"{re.escape(table)}\"|{re.escape(table)})(?=\s|\()"
    )
    create_sql, renamed = head.subn(f'CREATE TABLE "{staging}"', create_sql, count=1)
    if renamed != 1:
        raise MigrationFailedError(f"{table}: unrecognised CREATE TABLE statement")
    indexes = [
        str(r[0]) for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        )
    ]
    columns = [str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")')]
    column_list = ", ".join(f'"{c}"' for c in columns)
    select_list = ", ".join(
        f'{_MAP_FUNCTION}("{c}")' if c == id_column else f'"{c}"' for c in columns
    )
    conn.execute(create_sql)
    conn.execute(
        f'INSERT INTO "{staging}" ({column_list}) SELECT {select_list} FROM "{table}"'
    )
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{staging}" RENAME TO "{table}"')
    for index_sql in indexes:
        conn.execute(index_sql)


def _check_converted(plan: MigrationPlan) -> None:
    """The rewritten database is format 4 with the planned row counts and intact foreign keys.
    Raises MigrationFailedError otherwise."""
    conn = db.connect(plan.db_path, readonly=True)
    try:
        if formats.pdata_format(conn) != formats.PDATA_FORMAT_UUID_IDS:
            raise MigrationFailedError("the rewritten database is not format 4")
        if _group_counts(conn) != plan.group_counts:
            raise MigrationFailedError("row counts per record group changed during the rewrite")
        tables = tuple((t, _count(conn, t)) for t in _extension_tables(conn))
        if tables != plan.extension_tables:
            raise MigrationFailedError("extension table row counts changed during the rewrite")
        watermark = _count(conn, _WATERMARK_TABLE) if _table_exists(
            conn, _WATERMARK_TABLE
        ) else 0
        if watermark != plan.watermark_rows:
            raise MigrationFailedError("verify watermark row count changed during the rewrite")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise MigrationFailedError("the rewritten database fails PRAGMA foreign_key_check")
    finally:
        conn.close()


def _known_fail_issues(
    project: str, db_path: Path, *, include_row_count_parity: bool,
) -> Counter[tuple[str, str | None]]:
    """FAIL-severity verify issues the database already has, by (check, record group), from the
    read-only checks. A verify FAIL after the rewrite fails the migration only when it is not one
    of these. Before the rewrite both checks give the true baseline; on a database an earlier
    run already converted only file_path resolution, which ids never affect, can be excused."""
    conn = db.connect(db_path, readonly=True)
    try:
        issues = verify.check_file_path_resolution(conn, project, since=None)
        if include_row_count_parity:
            issues.extend(verify.check_row_count_parity(conn, project))
    finally:
        conn.close()
    return Counter((i.check, i.record_group) for i in issues if i.severity == "FAIL")


def _verify_against(project: str, baseline: Counter[tuple[str, str | None]]) -> None:
    summary = verify.run_verify(project=project, full=True)
    after = Counter(
        (i.check, i.record_group) for i in summary.issues if i.severity == "FAIL"
    )
    new = after - baseline
    if new:
        messages = [
            i.message for i in summary.issues
            if i.severity == "FAIL" and (i.check, i.record_group) in new
        ]
        raise MigrationFailedError(f"verify failed after the rewrite: {'; '.join(messages)}")


def _record_marker(project: str) -> None:
    conn = repository.connect(project)
    try:
        db.record_migration(
            conn, formats.UUID_IDS_MIGRATION, applied_at=repository._now_iso(),
        )
    finally:
        conn.close()


def _publish(project: str, *, project_root: Path) -> str:
    machine_id = machine_identity.resolve().machine_id
    conn = repository.connect(project)
    try:
        dump.write_latest(
            conn, project_root=project_root, machine_id=machine_id,
            vector=vector_clock_store.read_vector(conn),
        )
    finally:
        conn.close()
    return machine_id


# ------------------------------------------------------------------ adoption (later machines)


def _adopt(plan: MigrationPlan, *, project_root: Path) -> MigrationResult:
    try:
        backup_path = _take_backup(plan)
    except (OSError, sqlite3.Error, MigrationFailedError) as exc:
        return _failed(plan, backup_path=None, error=exc, restored=False)

    latest = dump.latest_path(project_root)
    try:
        replacement = rehydrate.build_replacement(
            plan.db_path.parent, dump.sql_body(latest.read_text()),
        )
    except (OSError, sqlite3.Error) as exc:
        return _failed(plan, backup_path=backup_path, error=exc, restored=False)
    try:
        conn = sqlite3.connect(str(replacement))
        try:
            if formats.pdata_format(conn) != formats.PDATA_FORMAT_UUID_IDS:
                raise MigrationFailedError(f"{latest} does not hold a format 4 database")
        finally:
            conn.close()
        replacement.replace(plan.db_path)
    except (OSError, sqlite3.Error, MigrationFailedError) as exc:
        replacement.unlink(missing_ok=True)
        return _failed(plan, backup_path=backup_path, error=exc, restored=False)
    rehydrate.drop_stale_wal_sidecars(plan.db_path)

    messages = [f"backup: {backup_path}", f"adopted {latest}"]
    if not _marker_recorded(plan.db_path):
        _record_marker(plan.project)
        messages.append(f"recorded the {formats.UUID_IDS_MIGRATION} marker")
    return MigrationResult(
        project=plan.project, outcome=Outcome.ADOPTED, plan=plan, backup_path=backup_path,
        messages=tuple(messages),
    )


# ------------------------------------------------------------------ resume / already migrated


def _marker_recorded(db_path: Path) -> bool:
    conn = db.connect(db_path, readonly=True)
    try:
        return _table_exists(conn, "migrations") and db.migration_applied(
            conn, formats.UUID_IDS_MIGRATION
        )
    finally:
        conn.close()


def _resume(project: str, *, db_path: Path, project_root: Path, write: bool) -> MigrationResult:
    """A format 4 database: finish what an earlier run left undone, or report it done. A
    database converted by this command has a migration origin; when it has no marker the run
    stopped before verify, and when the format 4 dump is missing it stopped before publishing.
    A database created as format 4 has no origin and nothing to publish."""
    conn = db.connect(db_path, readonly=True)
    try:
        origin = vector_clock_store.read_migration_origin(conn)
    finally:
        conn.close()
    marker = _marker_recorded(db_path)
    steps: list[str] = []
    if not marker:
        steps.extend([_STEP_VERIFY, _STEP_MARKER] if origin else [_STEP_MARKER])
    if origin and not dump.read_latest(project_root).present:
        steps.append(_STEP_PUBLISH)
    if not steps:
        return MigrationResult(
            project=project, outcome=Outcome.ALREADY_MIGRATED,
            messages=(f"already migrated - {db_path} is format 4; nothing to do",),
        )

    preconditions = (
        Precondition("database is format 4 (converted by an earlier run)", True),
        *_quiet_preconditions(db_path, project_root=project_root),
    )
    plan = MigrationPlan(
        project=project, db_path=db_path, path=MigrationPath.RESUME, group_counts=(),
        extension_tables=(), watermark_rows=0, format3_vector=origin,
        preconditions=preconditions, suspicious_fields=(), pending_steps=tuple(steps),
    )
    if not plan.ready:
        return MigrationResult(project=project, outcome=Outcome.REFUSED, plan=plan)
    if not write:
        return MigrationResult(project=project, outcome=Outcome.PLANNED, plan=plan)

    messages: list[str] = []
    try:
        if _STEP_VERIFY in steps:
            _verify_against(project, _known_fail_issues(
                project, db_path, include_row_count_parity=False,
            ))
            messages.append("verify passed")
        if _STEP_MARKER in steps:
            _record_marker(project)
            messages.append(f"recorded the {formats.UUID_IDS_MIGRATION} marker")
        if _STEP_PUBLISH in steps:
            machine_id = _publish(project, project_root=project_root)
            messages.append(
                f"published {dump.latest_path(project_root)} (machine_id={machine_id})"
            )
    except (OSError, sqlite3.Error, MigrationFailedError) as exc:
        return _failed(plan, backup_path=None, error=exc, restored=False)
    return MigrationResult(
        project=project, outcome=Outcome.RESUMED, plan=plan, messages=tuple(messages),
    )


# ------------------------------------------------------------------ rendering


def exit_status(outcome: Outcome) -> int:
    """The command's exit status for one project: 1 when it refused, 2 when it failed after the
    backup, else 0."""
    if outcome is Outcome.REFUSED:
        return 1
    if outcome is Outcome.FAILED:
        return 2
    return 0


def render(result: MigrationResult) -> list[str]:
    """The human-readable report for one project, as lines, for the CLI to print."""
    lines = [f"{result.project}: {_headline(result)}"]
    plan = result.plan
    if plan is not None and result.outcome in (Outcome.PLANNED, Outcome.REFUSED):
        lines.extend(_render_plan(plan))
    lines.extend(f"  {m}" for m in result.messages)
    return lines


_HEADLINES: Final = {
    Outcome.NOTHING_TO_MIGRATE: "nothing to migrate",
    Outcome.ALREADY_MIGRATED: "already migrated",
    Outcome.PLANNED: "dry run - nothing was changed",
    Outcome.REFUSED: "refused - nothing was changed",
    Outcome.MIGRATED: "migrated to format 4",
    Outcome.ADOPTED: "migrated to format 4 by adopting the published format 4 dump",
    Outcome.RESUMED: "finished an earlier migration",
    Outcome.FAILED: "FAILED",
}


def _headline(result: MigrationResult) -> str:
    return _HEADLINES[result.outcome]


def _render_plan(plan: MigrationPlan) -> list[str]:
    lines = [f"  database: {plan.db_path}"]
    if plan.path is MigrationPath.RESUME:
        lines.append(f"  plan: finish an earlier run - {', '.join(plan.pending_steps)}")
    else:
        if plan.path is MigrationPath.ADOPT:
            lines.append("  plan: adopt the published format 4 dump (another machine migrated "
                         "first); this database is backed up, then replaced by the dump's data")
        else:
            lines.append(f"  plan: rewrite every id to uuid5(\"{plan.project}:<integer id>\"), "
                         "back up, verify, record the marker, publish the format 4 dump")
        lines.append("  records per group (including soft-deleted):")
        lines.extend(
            f"    {g.record_group}: {g.rows} ({g.deleted} soft-deleted)"
            for g in plan.group_counts
        )
        if not plan.group_counts:
            lines.append("    (none)")
        lines.append("  extension tables:")
        lines.extend(f"    {table}: {rows} row(s)" for table, rows in plan.extension_tables)
        if not plan.extension_tables:
            lines.append("    (none)")
        lines.append(f"  verify watermark rows: {plan.watermark_rows}")
        vector = ", ".join(f"{k}={v}" for k, v in sorted(plan.format3_vector.items()))
        lines.append(f"  format 3 vector: {vector or '(empty)'}")
    lines.append("  preconditions:")
    for p in plan.preconditions:
        lines.append(f"    [{'ok' if p.holds else 'NO'}] {p.name}")
        if not p.holds:
            lines.append(f"         {p.remedy}")
    if plan.suspicious_fields:
        lines.append("  fields whose names suggest they hold record ids (ccst does not rewrite "
                     "them; audit them before --write):")
        lines.extend(f"    {f}" for f in plan.suspicious_fields)
    if plan.ready:
        lines.append("  every precondition holds: re-run with --write to migrate")
    return lines
