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

from cc_session_tools.lib.pdata import backup, store

PROJECTS_ROOT_ENV = "CCST_PROJECTS_ROOT"
# Permanent tool state, not a draft - a project's pdata classification/migration record. Named
# with a .pdata- prefix so it reads that way; LEGACY_PROPOSAL_FILENAME is the pre-rename name,
# still resolved via resolve_proposal_path() so an already-migrated project needs no manual
# rename.
PROPOSAL_FILENAME = ".pdata-migration-manifest.json"
LEGACY_PROPOSAL_FILENAME = ".ccst-pdata-proposal.json"
MIGRATED_ARCHIVE_DIRNAME = ".pdata-migrated"
MIGRATED_MANIFEST_FILENAME = "MANIFEST.md"
REHEARSAL_DB_DIRNAME = ".ccst-pdata-rehearsal-db"
REHEARSAL_BACKUP_DIRNAME = ".ccst-pdata-rehearsal-backups"

# Directories the classifier (classify.py) never walks into — repo/tool bookkeeping, not
# project content in the sense spec §7.1's classification pass cares about. Both rehearsal
# sandboxes are here so a reclassification pass never proposes a rehearsal's own .db or
# backup tarball as project content.
EXCLUDED_DIR_NAMES = frozenset({
    ".git", ".claude", "cc-sessions", MIGRATED_ARCHIVE_DIRNAME, REHEARSAL_DB_DIRNAME,
    REHEARSAL_BACKUP_DIRNAME,
})


def default_projects_root() -> Path:
    override = os.environ.get(PROJECTS_ROOT_ENV)
    return Path(override).expanduser() if override else Path.home() / "cc"


def resolve_proposal_path(project_root: Path) -> Path:
    """The manifest path to read/write for project_root: the new name if it already exists,
    else the legacy pre-rename name if that's what this project actually has on disk, else the
    new name (for a project writing its manifest for the first time). Every reader/writer of the
    manifest calls this instead of hardcoding project_root / PROPOSAL_FILENAME, so the rename is
    transparent to any project migrated before it."""
    new_path = project_root / PROPOSAL_FILENAME
    if new_path.exists():
        return new_path
    legacy_path = project_root / LEGACY_PROPOSAL_FILENAME
    if legacy_path.exists():
        return legacy_path
    return new_path


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


@contextmanager
def backup_dir_override(rehearse: Path | None) -> Iterator[None]:
    """Redirect backup.create_backup() to a self-contained rehearsal directory
    instead of the real backup_dir() (paths.data_home()/"pdata-backups" or
    whatever CCST_PDATA_BACKUP_DIR is already set to), by reusing this plan's own
    backup.BACKUP_DIR_ENV seam — mirrors project_db_dir_override's shape exactly.
    Without this, a rehearsed --write would still deposit a
    <project>-<YYYYMMDD-HHMMSS>.tar.gz into the same directory a genuine production
    migration's backup would use, indistinguishable by filename. No-op when
    rehearse is None."""
    if rehearse is None:
        yield
        return
    override_dir = rehearse / REHEARSAL_BACKUP_DIRNAME
    previous = os.environ.get(backup.BACKUP_DIR_ENV)
    os.environ[backup.BACKUP_DIR_ENV] = str(override_dir)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(backup.BACKUP_DIR_ENV, None)
        else:
            os.environ[backup.BACKUP_DIR_ENV] = previous
