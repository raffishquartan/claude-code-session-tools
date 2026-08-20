"""Pre-cutover backup snapshot for `ccst pdata init` (spec §7.1 step 5)."""
from __future__ import annotations

import os
import sqlite3
import tarfile
import time
from collections.abc import Callable
from pathlib import Path

from cc_session_tools.lib import db, paths
from cc_session_tools.lib.pdata import store

BACKUP_DIR_ENV = "CCST_PDATA_BACKUP_DIR"

# Transient I/O errors on network/DrvFS-backed project roots (e.g. a OneDrive-synced path
# mounted into WSL2) are often momentary — retry a few times before giving up rather than
# crashing the whole --write run on the first blip.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_SECONDS = 1


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff (1s, 2s, 4s, ...) keyed only off the attempt number — unlike a
    fixed-length lookup tuple, this can't drift out of sync with _MAX_ATTEMPTS if that
    constant is ever changed without a matching edit elsewhere."""
    return _RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))


class BackupError(Exception):
    """create_backup() exhausted its retries against a persistent OSError or sqlite3.Error."""


def backup_dir() -> Path:
    override = os.environ.get(BACKUP_DIR_ENV)
    return Path(override).expanduser() if override else paths.data_home() / "pdata-backups"


def _snapshot_db_into_tar(tar: tarfile.TarFile, *, project: str, tmp_dir: Path) -> None:
    """Add a point-in-time consistent copy of the project's .db to the archive, via
    db.backup_to() (the sqlite3 backup API, WAL-safe — a raw file copy of a WAL-mode database
    can miss rows still sitting in the -wal file that haven't been checkpointed into the main
    file yet) rather than a raw copy. No-op if the project has no .db yet (a brand-new
    project's first --write).

    Added under a `_pdata-db/` prefix distinct from the `<project>/` prefix that
    `create_backup()` uses for project_root's own contents — this snapshot is CCST's own
    tool state (the project's SQLite store lives under store.db_path(), not project_root),
    not project content, so a human restoring `<project>/...` from this archive into
    project_root must never end up with a stray `.db` file mixed into their project files.

    The destination is a throwaway temp file immediately tar'd and deleted, not a persistent
    store — db.backup_to() opens it via a plain (non-WAL) connection for exactly that reason,
    since routing a file that exists for a few lines and is never reopened through the
    WAL-enabling helper would just leave -wal/-shm sidecars to clean up."""
    db_source = store.db_path(project)
    if not db_source.exists():
        return
    snapshot_path = tmp_dir / f"{project}.db"
    try:
        db.backup_to(db_source, snapshot_path)
        tar.add(snapshot_path, arcname=f"_pdata-db/{project}.db")
    finally:
        snapshot_path.unlink(missing_ok=True)


def create_backup(
    *, project: str, project_root: Path,
    on_progress: Callable[[str], None] | None = None,
) -> Path:
    """tar.gz snapshot of project_root as it stands right now, written outside
    project_root (spec §7.1 step 5 — "stored outside the project folder, before
    touching any original file"). One backup per --write invocation; never
    overwritten or extended incrementally, matching the big-bang migration
    philosophy (spec goals).

    Written to a temp path first and atomically renamed to the final
    `<project>-<epoch>.tar.gz` name only once the archive is fully and successfully written —
    a failed or interrupted run never leaves a corrupt file at the name a valid backup would
    use. Transient OSErrors (e.g. a flaky network/DrvFS-backed project_root) are retried a
    bounded number of times before raising BackupError. Each failed attempt is reported
    through on_progress (if given) before the retry sleep — otherwise a multi-attempt backup
    that eventually succeeds leaves no trace in --write's progress log of the retries it took,
    and a backup that eventually fails only ever shows the final BackupError, not what each
    individual attempt actually hit.

    sqlite3.Error is caught alongside OSError because it is NOT an OSError subclass, yet
    _snapshot_db_into_tar's sqlite3 backup-API call can raise one (e.g. sqlite3.OperationalError
    once db.connect()'s 5s busy-timeout is exceeded against a locked source .db, or a disk I/O
    error surfacing through the sqlite3 backup API) — without this it would escape unwrapped,
    skip the retry/backoff below, and leave tmp_path behind.
    """
    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / f"{project}-{int(time.time())}.tar.gz"
    tmp_path = final_path.parent / (final_path.name + ".tmp")

    last_exc: OSError | sqlite3.Error | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                _snapshot_db_into_tar(tar, project=project, tmp_dir=target_dir)
                tar.add(project_root, arcname=project)
            os.replace(tmp_path, final_path)
            return final_path
        except (OSError, sqlite3.Error) as exc:
            last_exc = exc
            tmp_path.unlink(missing_ok=True)
            if on_progress is not None:
                on_progress(f"  backup attempt {attempt}/{_MAX_ATTEMPTS} failed: {exc}")
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_backoff_seconds(attempt))
    raise BackupError(
        f"backup of {project!r} failed after {_MAX_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc
