"""Per-project data-store path resolution: ~/.local/share/claude/project-db/<project>.db.

One SQLite .db per project (spec §4.1) — distinct from this repo's own one-file-per-subsystem
CCST-infra stores (ccmsg.db, ccsched.db, ...), which live flat in data_home() itself.
"""
from __future__ import annotations

import os
from pathlib import Path

from cc_session_tools.lib import paths

PROJECT_DB_DIR_ENV = "CCST_PROJECT_DB_DIR"

# Same literal as init_paths.PROJECTS_ROOT_ENV, duplicated rather than imported — init_paths.py
# already imports store.py (for PROJECT_DB_DIR_ENV/validate_project_name), so store.py importing
# init_paths.py back would invert this package's layering (store.py is the leaf module every
# other pdata module, including init_paths.py, is built on). A single short string literal is
# cheaper to keep in sync by hand than to justify a third shared module for.
PROJECTS_ROOT_ENV = "CCST_PROJECTS_ROOT"


def project_db_dir() -> Path:
    """Directory holding every project's <project>.db. CCST_PROJECT_DB_DIR overrides the
    default paths.data_home() / "project-db" (tests redirect via the env var)."""
    override = os.environ.get(PROJECT_DB_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return paths.data_home() / "project-db"


def validate_project_name(project: str) -> None:
    """Reject a project name that isn't safe to use as a single filesystem path component.

    project is interpolated directly into a file path (project-db/<project>.db) — this is a
    path-traversal boundary check, not a spec-mandated naming convention.
    """
    if not project or project in (".", "..") or "/" in project or "\\" in project:
        raise ValueError(f"invalid project name: {project!r}")


def db_path(project: str) -> Path:
    validate_project_name(project)
    return project_db_dir() / f"{project}.db"


def project_root(project: str) -> Path:
    """The project's source-tree root: <projects_root>/<project>, where projects_root is
    CCST_PROJECTS_ROOT if set, else Path.home() / "cc" — the same resolution
    init_paths.default_projects_root() applies, duplicated here (see PROJECTS_ROOT_ENV above)
    so rehydrate.py can resolve a project root without importing init_paths.py's whole surface
    for one path. Unlike init_paths.resolve_project_root(), this never creates the directory —
    callers here (dump/rehydrate) only ever read from it."""
    validate_project_name(project)
    override = os.environ.get(PROJECTS_ROOT_ENV)
    root = Path(override).expanduser() if override else Path.home() / "cc"
    return root / project
