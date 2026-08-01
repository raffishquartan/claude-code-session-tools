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
