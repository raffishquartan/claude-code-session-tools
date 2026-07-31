"""Path/env resolution for `ccst pdata init` (spec §7): where a project's source tree
lives, where its dry-run classification proposal is written, and how --rehearse
redirects both the project root and the .db target without touching the real
project or Plan A's shipped connection helpers.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cc_session_tools.lib.pdata import store

PROJECTS_ROOT_ENV = "CCST_PROJECTS_ROOT"
PROPOSAL_FILENAME = ".ccst-pdata-proposal.json"
MIGRATED_ARCHIVE_DIRNAME = ".pdata-migrated"
MIGRATED_MANIFEST_FILENAME = "MANIFEST.md"
REHEARSAL_DB_DIRNAME = ".ccst-pdata-rehearsal-db"

# Directories the classifier (classify.py) never walks into — repo/tool bookkeeping, not
# project content in the sense spec §7.1's classification pass cares about.
EXCLUDED_DIR_NAMES = frozenset({
    ".git", ".claude", "cc-sessions", MIGRATED_ARCHIVE_DIRNAME, REHEARSAL_DB_DIRNAME,
})


def default_projects_root() -> Path:
    override = os.environ.get(PROJECTS_ROOT_ENV)
    return Path(override).expanduser() if override else Path.home() / "cc"


def resolve_project_root(project: str, *, rehearse: Path | None) -> Path:
    """The directory ccst pdata init reads/writes files against: --rehearse's path
    when given (spec §7.1 step 0 — the copy already happened before this call), else
    <projects_root>/<project>, created if this is a genuinely new project (spec:
    "safe to run against an empty folder... also how a genuinely new project gets
    its .db")."""
    store.validate_project_name(project)
    if rehearse is not None:
        return rehearse
    root = default_projects_root() / project
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def project_db_dir_override(rehearse: Path | None) -> Iterator[None]:
    """Redirect every service.py/repository.py call inside the `with` block to a
    self-contained rehearsal .db instead of the real project-db/<project>.db, by
    reusing Plan A's own CCST_PROJECT_DB_DIR test seam (store.PROJECT_DB_DIR_ENV) —
    rehearsal touches zero Plan A code. No-op when rehearse is None."""
    if rehearse is None:
        yield
        return
    override_dir = rehearse / REHEARSAL_DB_DIRNAME
    previous = os.environ.get(store.PROJECT_DB_DIR_ENV)
    os.environ[store.PROJECT_DB_DIR_ENV] = str(override_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(store.PROJECT_DB_DIR_ENV, None)
        else:
            os.environ[store.PROJECT_DB_DIR_ENV] = previous
