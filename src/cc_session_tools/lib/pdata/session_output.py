"""Session-output index (spec §8): registers cc-sessions/*/out/ deliverables into the
'session-output' record_group on Plan A's unmodified ccst pdata schema/CLI, and reconciles that
index against disk via a 7-day ccsched job. Pure orchestration on top of lib.pdata.service —
this module adds no new tables or CLI primitives to Plan A's schema itself."""
from __future__ import annotations

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
