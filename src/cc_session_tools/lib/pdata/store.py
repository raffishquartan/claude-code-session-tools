"""Per-project data-store path resolution: ~/.local/share/claude/project-db/<project>.db.

One SQLite .db per project (spec §4.1) — distinct from this repo's own one-file-per-subsystem
CCST-infra stores (ccmsg.db, ccsched.db, ...), which live flat in data_home() itself.
"""
from __future__ import annotations

import os
from pathlib import Path

from cc_session_tools.lib import paths

PROJECT_DB_DIR_ENV = "CCST_PROJECT_DB_DIR"


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
