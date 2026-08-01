"""Session-output index (spec §8): registers cc-sessions/*/out/ deliverables into the
'session-output' record_group on Plan A's unmodified ccst pdata schema/CLI, and reconciles that
index against disk via a 7-day ccsched job. Pure orchestration on top of lib.pdata.service —
this module adds no new tables or CLI primitives to Plan A's schema itself."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cc_session_tools.lib import roots

if TYPE_CHECKING:
    # Type-only import: every runtime use of `service` in this module is a local import inside
    # the function that needs it (see e.g. ensure_session_output_schema, reconcile_project),
    # matching this module's (and the rest of the codebase's) convention of deferring
    # lib.pdata.service imports to keep CLI startup fast. `_get_watermark_record`'s return
    # annotation (below) still needs the real `Record` type for mypy --strict's
    # disallow-untyped-defs check — a TYPE_CHECKING-only import gives mypy the name without
    # adding a runtime import to the module's top level.
    from cc_session_tools.lib.pdata.service import Record

SESSION_OUTPUT_GROUP = "session-output"
WATERMARK_GROUP = "session-output-watermark"


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    project: str
    scanned: int
    registered: int
    watermark: int


def discover_projects_with_sessions() -> list[tuple[str, Path]]:
    """(project_name, project_root) for every direct subdirectory of a configured session root
    that contains a cc-sessions/ directory — the same signal the pm-update-central-files skill
    itself depends on. Deliberately narrower than "every directory under a session root" (see
    plan Decision 4): most ~/repos/* entries are ordinary code repos with no session history,
    and connecting to a nonexistent project would silently create an empty project .db for each
    one. Raises roots.RootsConfigError if neither session root env var is configured — same
    contract as roots.load_session_roots().
    """
    found: dict[str, Path] = {}
    for root in roots.load_session_roots():
        for entry in sorted(root.iterdir()):
            if entry.name in found:
                continue
            if entry.is_dir() and (entry / "cc-sessions").is_dir():
                found[entry.name] = entry
    return sorted(found.items())


def find_project_root(project: str) -> Path | None:
    """The configured session root's copy of <project>, if it has a cc-sessions/ directory.
    Used by the single-project (--project NAME) CLI path; --all-projects uses
    discover_projects_with_sessions() directly. Raises roots.RootsConfigError under the same
    conditions as discover_projects_with_sessions()."""
    for name, root in discover_projects_with_sessions():
        if name == project:
            return root
    return None


def find_new_out_files(project_root: Path, *, since_mtime: int) -> list[tuple[Path, int]]:
    """Every regular file under <project_root>/cc-sessions/*/out/ (any depth) whose mtime is at
    or after since_mtime, as (absolute_path, mtime_epoch_seconds) pairs. Returns [] if
    project_root has no cc-sessions/ directory at all (a genuinely new/empty project — same
    "safe against an empty folder" stance as the rest of this feature).

    Deliberately >= rather than a strict >: mtime is truncated to whole seconds (see the `int()`
    below), so two files written within the same wall-clock second can share an mtime. A strict >
    against a watermark that already equals that second would permanently exclude whichever of
    those files gets scanned after the watermark has already advanced to it — a silent,
    unrecoverable gap in the safety-net job's own coverage. >= re-admits already-registered files
    as *candidates* every time their mtime matches the watermark, but `_is_already_registered`'s
    file_path dedupe check makes re-including them a no-op — a little repeated scanning work,
    never a repeated write, and never a missed file."""
    cc_sessions = project_root / "cc-sessions"
    if not cc_sessions.is_dir():
        return []
    results: list[tuple[Path, int]] = []
    for session_dir in sorted(cc_sessions.iterdir()):
        out_dir = session_dir / "out"
        if not out_dir.is_dir():
            continue
        for file in sorted(out_dir.rglob("*")):
            if not file.is_file():
                continue
            mtime = int(file.stat().st_mtime)
            if mtime >= since_mtime:
                results.append((file, mtime))
    return results


def session_tag_from_relpath(rel_path: str) -> str:
    """Extract the <session_tag> from a 'cc-sessions/<session_tag>/out/...' relative path (the
    shape every path passed here is guaranteed to have, since it always comes from
    find_new_out_files's own walk — this raises loudly rather than guessing if that ever stops
    being true, per this repo's 'throw loudly on an impossible state' coding standard)."""
    parts = Path(rel_path).parts
    if len(parts) < 4 or parts[0] != "cc-sessions" or parts[2] != "out":
        raise ValueError(
            f"expected a 'cc-sessions/<tag>/out/...' path, got {rel_path!r}"
        )
    return parts[1]


def ensure_session_output_schema(project: str) -> None:
    """Idempotent — safe to call on every reconcile run and from the pm-update-central-files
    skill's own AUTO item (schema_add_field no-ops if the column already exists).

    Also creates a partial index on records(file_path), scoped to record_group='session-output'
    via a SQLite partial-index WHERE clause. Without it, _is_already_registered's per-file dedupe
    check — and the equivalent `ccst pdata query --where "file_path = ..."` call the
    pm-update-central-files skill's own AUTO item makes — is an unindexed scan across every
    session-output row this project has ever accumulated (the base schema indexes only
    record_group and updated_at). That scan grows without bound for a catch-all project like
    `oneshot`, contradicting spec Goal G5 ("cost never scales with accumulated history"). Scoping
    the index to this one record_group keeps it a session-output-only concern rather than a
    change to the shared base-schema index list."""
    from cc_session_tools.lib.pdata import repository, service

    service.schema_add_field(
        project=project,
        record_group=SESSION_OUTPUT_GROUP,
        field_name="session_tag",
        sql_type="TEXT",
        description="The cc-sessions/<session_tag>/ directory this out/ file was produced by",
        default=None,
    )

    conn = repository.connect(project)
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_session_output_file_path "
            f"ON records(file_path) WHERE record_group = '{SESSION_OUTPUT_GROUP}'"
        )
        conn.commit()
    finally:
        conn.close()


def _get_watermark_record(project: str) -> Record | None:
    from cc_session_tools.lib.pdata import service, store

    if not store.db_path(project).exists():
        # A project with no .db file yet has no watermark row, by definition. Short-circuits
        # before repository.connect() (called inside service.list_records), which always
        # creates the .db file and its base schema as a side effect of connecting — even for a
        # read-only lookup. Without this check, get_watermark() would create the project's .db
        # file on every call, including reconcile_project's dry_run=True branch, contradicting
        # its docstring's claim that dry-run "writes nothing". set_watermark()'s own create path
        # (record is None) still creates the file as intended, via its own service.add_record
        # call further down — this short-circuit only skips the unnecessary read-only connect.
        return None

    records = service.list_records(project=project, record_group=WATERMARK_GROUP, limit=1)
    return records[0] if records else None


def get_watermark(project: str) -> int:
    """The epoch-seconds cursor of the last file successfully reconciled for this project, or 0
    if reconciliation has never run (a full backfill on first run, same "safe against an empty
    baseline" stance as the rest of this feature)."""
    record = _get_watermark_record(project)
    return int(record.content) if record is not None else 0


def set_watermark(project: str, epoch: int) -> None:
    """Create-or-update the single session-output-watermark row for this project (spec §4.3
    singleton-state pattern). On a version conflict (a concurrent writer, e.g. a manual
    `ccst pdata reconcile-session-output --project <name>` run landing at the same moment as the
    scheduled job's own `--all-projects` sweep; the pm-update-central-files skill's AUTO item
    never calls this function at all), refetch and retry exactly once; a conflict that persists
    after the retry propagates rather than being silently discarded."""
    from cc_session_tools.lib.pdata import service

    record = _get_watermark_record(project)
    if record is None:
        service.add_record(
            project=project, record_group=WATERMARK_GROUP, content=str(epoch),
            file_path=None, fields={}, created_at=epoch,
        )
        return
    try:
        service.update_record(
            project=project, record_id=record.id, expected_version=record.version,
            content=str(epoch), file_path=None, fields={}, updated_at=epoch,
        )
    except service.VersionConflictError:
        record = _get_watermark_record(project)
        assert record is not None
        service.update_record(
            project=project, record_id=record.id, expected_version=record.version,
            content=str(epoch), file_path=None, fields={}, updated_at=epoch,
        )


def _is_already_registered(project: str, rel_path: str) -> bool:
    from cc_session_tools.lib.pdata import service, store

    if not store.db_path(project).exists():
        # A project with no .db file yet has no registered rows, by definition. Short-circuits
        # before repository.connect() (called inside service.query_records), which always
        # creates the .db file and its base schema as a side effect of connecting — even for a
        # read-only lookup (repository.connect() applies ddl=_BASE_DDL on every connect, with no
        # readonly path). Without this check, reconcile_project's dry_run=True branch would
        # create the project's .db file via this very lookup, contradicting its own docstring's
        # claim that dry-run "writes nothing".
        return False

    matches = service.query_records(
        project=project, record_group=SESSION_OUTPUT_GROUP,
        where=[f"file_path = {rel_path}"], limit=1,
    )
    return len(matches) > 0


def reconcile_project(
    project: str, project_root: Path, *, dry_run: bool = False,
) -> ReconcileResult:
    """Backfill session-output rows for every cc-sessions/*/out/ file newer than this project's
    watermark that isn't already registered (spec §8 item 2). Safe to call repeatedly, safe to
    call late, safe to coalesce (the ccsched contract, see manage-recurring-cc-jobs-using-ccsched
    skill) — every insert is guarded by _is_already_registered's file_path dedupe check, and the
    watermark only ever advances, never rewinds. dry_run reports what WOULD be registered without
    writing anything (including the watermark)."""
    from cc_session_tools.lib.pdata import service

    if not dry_run:
        ensure_session_output_schema(project)

    since = get_watermark(project)
    candidates = find_new_out_files(project_root, since_mtime=since)

    registered = 0
    watermark = since
    for file_path, mtime in candidates:
        watermark = max(watermark, mtime)
        rel_path = str(file_path.relative_to(project_root))
        if _is_already_registered(project, rel_path):
            continue
        if not dry_run:
            service.add_record(
                project=project, record_group=SESSION_OUTPUT_GROUP,
                content=file_path.name, file_path=rel_path,
                fields={"session_tag": session_tag_from_relpath(rel_path)},
                created_at=mtime,
            )
        registered += 1

    if not dry_run and watermark > since:
        set_watermark(project, watermark)

    return ReconcileResult(
        project=project, scanned=len(candidates), registered=registered, watermark=watermark,
    )
