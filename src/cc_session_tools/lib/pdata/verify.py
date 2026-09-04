"""Integrity-check backstop for ccst pdata (spec §6.3, §8.2).

Three checks per project, layered on top of the shipped store and (where
migration history exists) the archived migration record: row-count parity,
file_path resolution, and suspiciously-close-in-time double-updates. Results
are persisted here, never recomputed live by `ccst doctor` — a recurring
ccsched job (`pdata-verify-all`, registered in the shared
lib/scheduler/bundled_jobs.py) produces them; doctor only reads the most
recent one (verify.last_run()).

This module owns two tables of its own (pdata_verify_watermark,
pdata_verify_runs), created lazily via ensure_verify_tables() on top of the
connection repository.connect() already opens. The store, migration and
importer modules are never modified by this module — only their already-public
functions are called.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from cc_session_tools.lib.pdata import importers, init_paths, manifest, repository, store

_VERIFY_DDL = """
CREATE TABLE IF NOT EXISTS pdata_verify_watermark (
    record_id INTEGER PRIMARY KEY,
    last_seen_version INTEGER NOT NULL,
    last_seen_updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pdata_verify_runs (
    id INTEGER PRIMARY KEY,
    run_at INTEGER NOT NULL,
    full_scan INTEGER NOT NULL,
    status TEXT NOT NULL,
    issue_count INTEGER NOT NULL,
    details TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pdata_verify_runs_run_at ON pdata_verify_runs(run_at);
"""

_MAX_RETAINED_RUNS = 100
_DOUBLE_UPDATE_WINDOW_SECONDS = 6 * 60 * 60  # see plan Decision 4 — not a CLI flag

_SEVERITY_ORDER = {"OK": 0, "WARN": 1, "FAIL": 2}


@dataclass
class VerifyIssue:
    check: str
    severity: str
    record_group: str | None
    record_id: int | None
    message: str


@dataclass
class VerifySummary:
    project: str
    run_at: int
    full_scan: bool
    status: str
    issues: list[VerifyIssue] = field(default_factory=list)


def ensure_verify_tables(conn: sqlite3.Connection) -> None:
    """CREATE ... IF NOT EXISTS both verify-only tables — safe to call on
    every run, matching repository.connect()'s own connect-time DDL
    idempotency."""
    conn.executescript(_VERIFY_DDL)


def _count_base_records(
    conn: sqlite3.Connection, *, record_group: str, include_deleted: bool,
) -> int:
    """`COUNT(*)` against the base `records` table for one `record_group` — deliberately not
    `repository.list_base_records(...)` + `len(...)`, which would materialize every row's full
    `content` just to discard it, an O(row count) cost that contradicts row-count parity's own
    claim that it "doesn't grow with accumulated history" and spec G5 (doctor, and by extension
    verify's own checks, must never pay full-table-scan cost where a count suffices).
    `include_deleted=True` is what row-count-parity uses: a soft-deleted row still physically
    exists in the table (only `deleted_at` is set, spec §4.5) — only a row that has truly vanished
    from the table should ever trip this check, never an intentional `ccst pdata delete` (the
    growth-only model doesn't distinguish "grew via `add`" from "shrank via a legitimate `delete`"
    without this)."""
    if include_deleted:
        sql = "SELECT COUNT(*) AS c FROM records WHERE record_group=?"
    else:
        sql = "SELECT COUNT(*) AS c FROM records WHERE record_group=? AND deleted_at IS NULL"
    count: int = conn.execute(sql, (record_group,)).fetchone()["c"]
    return count


def _check_manifest_missing_with_evidence(
    conn: sqlite3.Connection, project_root: Path
) -> list[VerifyIssue]:
    """Called only when no manifest (new or legacy name) exists for this project. Distinguishes
    "genuinely never migrated" (nothing to flag - the manifest's absence is expected) from
    "migrated via `ccst pdata init`, then the manifest was lost" by checking the one signal that
    is unambiguous evidence of that specific flow: `.pdata-migrated/<record_group>/` archive
    content, written exclusively by cutover.py's classify-and-migrate step (confirmed - no other
    code path creates this directory).

    Deliberately does NOT treat "any populated record_group" as evidence on its own: a project
    can accumulate rows entirely through `ccst pdata add`/`service.add_record` without ever
    running `ccst pdata init`'s classify/cutover flow, and never has - or needs - a manifest.
    Row counts can't tell that apart from a real classify-then-lost-manifest case without the
    manifest itself to cross-reference against, so using them as an independent trigger here
    produced false positives against every project that only ever used `pdata add` directly."""
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    if not (archive_root.is_dir() and any(archive_root.iterdir())):
        return []  # genuinely never migrated via the classify/cutover flow - not a defect

    return [
        VerifyIssue(
            check="manifest-missing",
            severity="FAIL",
            record_group=None,
            record_id=None,
            message=(
                f"migrated, manifest now missing: {project_root} has archived files under "
                f"{init_paths.MIGRATED_ARCHIVE_DIRNAME}/ (written only by a completed "
                f"`ccst pdata init --write` cutover) but no {init_paths.PROPOSAL_FILENAME} "
                f"(or legacy {init_paths.LEGACY_PROPOSAL_FILENAME}) manifest file. Recovery: "
                "run `ccst pdata schema list --project <project>` for an overview of every "
                "record group the database currently has, and `ccst pdata schema show "
                "--project <project> --group <record_group>` to inspect a group's current "
                "field list - cross-reference both against the remaining "
                f"{init_paths.MIGRATED_ARCHIVE_DIRNAME}/<record_group>/ file listing to manually "
                "reconstruct what the manifest would have recorded."
            ),
        )
    ]


def check_row_count_parity(conn: sqlite3.Connection, project: str) -> list[VerifyIssue]:
    """For every db-owned entry in the project's classification proposal (spec §7.1) whose
    original file is still archived under .pdata-migrated/, recompute the row count the importer
    would have produced. Uses `importers.count_source_rows()` — the same function the init
    entry-count parity check (spec §7.1 step 4) uses — rather than `importers.import_entry()` +
    `len()`: `count_source_rows()` is built specifically to answer "how many rows should this
    source produce", cheaply (a line/row/section count), whereas `import_entry()` parses the
    entire archived file into full `ImportRow` objects (content, fields, file mtimes) only for
    every field but the count to be discarded here. Entries are grouped by `record_group` first
    and their expected counts summed *before* comparing — two entries feeding the same
    record_group (e.g. log.md + log.csv both classified into record_group="log") share one actual
    count, so comparing that shared count against each entry's expected count independently would
    let a loss that stays above the smaller entry's own threshold pass silently; comparing the
    summed expectation against the actual count once closes that gap. The actual count includes
    soft-deleted rows: a legitimate `ccst pdata delete` must never permanently trip this check —
    only a row that is actually gone from the table (never merely marked deleted_at) is evidence
    of loss. Skipped entirely if the project was genuinely never migrated (no manifest, no
    migration-evidence anywhere) — nothing to compare against, which is not itself a defect. But
    a manifest that is merely *missing* — while `.pdata-migrated/` content or populated record
    groups prove a migration did happen — is a distinct problem this function now raises as its
    own issue (see `_check_manifest_missing_with_evidence`), rather than being silently
    indistinguishable from the legitimate never-migrated case."""
    project_root = init_paths.default_projects_root() / project
    proposal_path = init_paths.resolve_proposal_path(project_root)
    if not proposal_path.exists():
        return _check_manifest_missing_with_evidence(conn, project_root)

    m = manifest.load(proposal_path)
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME

    expected_by_group: dict[str, int] = {}
    for entry in m.entries:
        if entry.classification != "db-owned":
            continue
        archived_path = archive_root / entry.path
        if not archived_path.exists():
            continue  # not yet cut over — nothing to compare against yet
        group = entry.db_group()
        expected_by_group[group] = (
            expected_by_group.get(group, 0) + importers.count_source_rows(archive_root, entry)
        )

    issues: list[VerifyIssue] = []
    for group, expected_rows in expected_by_group.items():
        actual_rows = _count_base_records(conn, record_group=group, include_deleted=True)
        if actual_rows < expected_rows:
            issues.append(VerifyIssue(
                check="row-count-parity", severity="FAIL",
                record_group=group, record_id=None,
                message=(
                    f"migrated source(s) for record_group {group!r} imply >= "
                    f"{expected_rows} row(s) (including soft-deleted), only "
                    f"{actual_rows} found — possible data loss"
                ),
            ))
    return issues


def check_file_path_resolution(
    conn: sqlite3.Connection, project: str, *, since: int | None,
) -> list[VerifyIssue]:
    """Every active record with a non-null file_path must resolve under the project root (spec
    §4.2's own resolution rule). Read-only: uses init_paths.default_projects_root() directly
    rather than init_paths.resolve_project_root(), which would mkdir a missing project directory
    as a side effect — inappropriate for a check that must never write. since=None (--full) checks
    every active row; otherwise only rows updated after since. Lists record groups via
    repository.list_record_groups(conn) directly against the connection this function was already
    handed, rather than service.schema_list(project=project) — the latter opens and closes a
    brand-new connection to the same project .db (its own WAL-pragma/busy-timeout setup included)
    purely to read the one list this function's own `conn` argument can already answer, an
    avoidable extra connection-open per run per project (spec G5: cost must never scale with
    unnecessary work)."""
    project_root = init_paths.default_projects_root() / project
    issues: list[VerifyIssue] = []
    for group in repository.list_record_groups(conn):
        record_group = str(group["record_group"])
        rows = repository.list_base_records(
            conn, record_group=record_group, since=since, until=None,
            limit=None, include_deleted=False,
        )
        for row in rows:
            file_path = row["file_path"]
            if file_path is None:
                continue
            if not (project_root / file_path).is_file():
                issues.append(VerifyIssue(
                    check="file-path-resolution", severity="FAIL",
                    record_group=record_group, record_id=row["id"],
                    message=(
                        f"record {row['id']}: file_path {file_path!r} does not "
                        f"resolve under {project_root}"
                    ),
                ))
    return issues


def check_suspicious_double_updates(
    conn: sqlite3.Connection, project: str, *, since: int | None,
) -> list[VerifyIssue]:
    """Flag an active row whose version has advanced by >=2 and whose updated_at has moved by no
    more than _DOUBLE_UPDATE_WINDOW_SECONDS since the last time verify looked at it — i.e. at
    least two updates landed inside one polling interval (spec §6.3). Always upserts every
    examined row's current (version, updated_at) into pdata_verify_watermark afterwards, so the
    next run's diff is against this run's state, regardless of whether an issue was raised this
    time. Caller must run this inside a transaction (repository._immediate) since it writes. Lists
    record groups via repository.list_record_groups(conn) directly against the already-open
    connection, not service.schema_list(project=project) — see check_file_path_resolution's
    docstring for why the latter is avoided here (an avoidable extra connection open per run per
    project, spec G5)."""
    issues: list[VerifyIssue] = []
    for group in repository.list_record_groups(conn):
        record_group = str(group["record_group"])
        rows = repository.list_base_records(
            conn, record_group=record_group, since=since, until=None,
            limit=None, include_deleted=False,
        )
        for row in rows:
            watermark = conn.execute(
                "SELECT last_seen_version, last_seen_updated_at "
                "FROM pdata_verify_watermark WHERE record_id=?",
                (row["id"],),
            ).fetchone()
            if watermark is not None:
                version_delta = row["version"] - watermark["last_seen_version"]
                time_delta = row["updated_at"] - watermark["last_seen_updated_at"]
                if version_delta >= 2 and time_delta <= _DOUBLE_UPDATE_WINDOW_SECONDS:
                    issues.append(VerifyIssue(
                        check="suspicious-double-update", severity="WARN",
                        record_group=record_group, record_id=row["id"],
                        message=(
                            f"record {row['id']} in {record_group!r}: version "
                            f"advanced by {version_delta} within {time_delta}s of "
                            f"the last verify pass — two updates landed unusually "
                            f"close together"
                        ),
                    ))
            conn.execute(
                "INSERT INTO pdata_verify_watermark "
                "(record_id, last_seen_version, last_seen_updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(record_id) DO UPDATE SET "
                "last_seen_version=excluded.last_seen_version, "
                "last_seen_updated_at=excluded.last_seen_updated_at",
                (row["id"], row["version"], row["updated_at"]),
            )
    return issues


def _worst_status(issues: list[VerifyIssue]) -> str:
    if not issues:
        return "OK"
    return max((i.severity for i in issues), key=lambda s: _SEVERITY_ORDER[s])


def _last_run_at(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(run_at) AS m FROM pdata_verify_runs").fetchone()
    last: int | None = row["m"] if row is not None else None
    return last


def _persist_run(
    conn: sqlite3.Connection, *, run_at: int, full_scan: bool, status: str,
    issues: list[VerifyIssue],
) -> None:
    details = json.dumps([
        {"check": i.check, "severity": i.severity, "record_group": i.record_group,
         "record_id": i.record_id, "message": i.message}
        for i in issues
    ])
    conn.execute(
        "INSERT INTO pdata_verify_runs (run_at, full_scan, status, issue_count, details) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_at, int(full_scan), status, len(issues), details),
    )
    conn.execute(
        # Order by id, not run_at: run_at is second-resolution and two runs in a tight loop
        # (as in the retention test) can share a value — id (the autoincrement rowid) is
        # strictly monotonic with insertion order regardless of clock resolution, so it is the
        # correct "most recent" tiebreak, here and in last_run() below.
        "DELETE FROM pdata_verify_runs WHERE id NOT IN ("
        "  SELECT id FROM pdata_verify_runs ORDER BY id DESC LIMIT ?"
        ")",
        (_MAX_RETAINED_RUNS,),
    )


def run_verify(*, project: str, full: bool = False) -> VerifySummary:
    """Run all three checks (spec §6.3) and persist the result. Row-count parity always runs in
    full; file_path resolution and the double-update check are scoped to rows changed since the
    last run unless full=True.

    Raises ValueError if `project` has no existing .db yet — repository.connect() would otherwise
    silently create one (its own CREATE TABLE IF NOT EXISTS side effect) and report a brand-new
    empty store as "clean", masking a typo'd or genuinely nonexistent project name behind a
    false-looking-fine result. This mirrors discover_projects()'s own "only .dbs that already
    exist" standard, applied here to a single explicitly-named project."""
    if not store.db_path(project).exists():
        raise ValueError(
            f"no data store found for project {project!r} — run 'ccst pdata add' "
            f"or 'ccst pdata init' for this project first"
        )
    conn = repository.connect(project)
    try:
        ensure_verify_tables(conn)
        since = None if full else _last_run_at(conn)

        issues: list[VerifyIssue] = []
        issues.extend(check_row_count_parity(conn, project))
        issues.extend(check_file_path_resolution(conn, project, since=since))

        run_at = int(time.time())
        with repository._immediate(conn):
            issues.extend(check_suspicious_double_updates(conn, project, since=since))
            status = _worst_status(issues)
            _persist_run(conn, run_at=run_at, full_scan=full, status=status, issues=issues)
        return VerifySummary(
            project=project, run_at=run_at, full_scan=full, status=status, issues=issues,
        )
    finally:
        conn.close()


def last_run(project: str) -> VerifySummary | None:
    """Read-only: the most recently persisted verify run, or None if verify has never run for
    this project (or its .db doesn't exist yet). Never triggers a run itself — ccst doctor calls
    this, not run_verify()."""
    if not store.db_path(project).exists():
        return None
    conn = repository.connect(project)
    try:
        ensure_verify_tables(conn)
        row = conn.execute(
            "SELECT * FROM pdata_verify_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        issues = [VerifyIssue(**d) for d in json.loads(row["details"])]
        return VerifySummary(
            project=project, run_at=row["run_at"], full_scan=bool(row["full_scan"]),
            status=row["status"], issues=issues,
        )
    finally:
        conn.close()


def discover_projects() -> list[str]:
    """Every project with a .db under project_db_dir(), sorted — used by both
    `ccst pdata verify --all-projects` and the doctor check, so a project need not have an
    on-disk folder yet to be found."""
    db_dir = store.project_db_dir()
    if not db_dir.is_dir():
        return []
    return sorted(p.stem for p in db_dir.glob("*.db"))
