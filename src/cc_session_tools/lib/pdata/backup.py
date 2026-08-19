"""Pre-cutover backup snapshot for `ccst pdata init` (spec §7.1 step 5)."""
from __future__ import annotations

import os
import sqlite3
import tarfile
import time
from pathlib import Path

from cc_session_tools.lib import db, paths
from cc_session_tools.lib.pdata import store

BACKUP_DIR_ENV = "CCST_PDATA_BACKUP_DIR"

# Transient I/O errors on network/DrvFS-backed project roots (e.g. a OneDrive-synced path
# mounted into WSL2) are often momentary — retry a few times before giving up rather than
# crashing the whole --write run on the first blip.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (1, 2)


class BackupError(Exception):
    """create_backup() exhausted its retries against a persistent OSError."""


def backup_dir() -> Path:
    override = os.environ.get(BACKUP_DIR_ENV)
    return Path(override).expanduser() if override else paths.data_home() / "pdata-backups"


def _snapshot_db_into_tar(tar: tarfile.TarFile, *, project: str, tmp_dir: Path) -> None:
    """Add a point-in-time consistent copy of the project's .db to the archive, via the
    sqlite3 backup API rather than a raw file copy — a raw copy of a WAL-mode database can
    miss rows still sitting in the -wal file that haven't been checkpointed into the main
    file yet. No-op if the project has no .db yet (a brand-new project's first --write).

    The source connection goes through the shared lib/db.py helper, readonly, matching this
    repo's data-store convention (WAL + busy-timeout applied consistently). The destination
    is a throwaway temp file immediately tar'd and deleted, not a persistent store, so it
    connects directly via sqlite3.connect() — routing it through the WAL-enabling helper
    would just leave -wal/-shm sidecars to clean up around a file that exists for a few
    lines and is never reopened."""
    db_source = store.db_path(project)
    if not db_source.exists():
        return
    snapshot_path = tmp_dir / f"{project}.db"
    try:
        src_conn = db.connect(db_source, readonly=True)
        try:
            dst_conn = sqlite3.connect(snapshot_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        tar.add(snapshot_path, arcname=f"{project}/{project}.db")
    finally:
        snapshot_path.unlink(missing_ok=True)


def create_backup(*, project: str, project_root: Path) -> Path:
    """tar.gz snapshot of project_root as it stands right now, written outside
    project_root (spec §7.1 step 5 — "stored outside the project folder, before
    touching any original file"). One backup per --write invocation; never
    overwritten or extended incrementally, matching the big-bang migration
    philosophy (spec goals).

    Written to a temp path first and atomically renamed to the final
    `<project>-<epoch>.tar.gz` name only once the archive is fully and successfully written —
    a failed or interrupted run never leaves a corrupt file at the name a valid backup would
    use. Transient OSErrors (e.g. a flaky network/DrvFS-backed project_root) are retried a
    bounded number of times before raising BackupError.
    """
    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / f"{project}-{int(time.time())}.tar.gz"
    tmp_path = final_path.parent / (final_path.name + ".tmp")

    last_exc: OSError | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                _snapshot_db_into_tar(tar, project=project, tmp_dir=target_dir)
                tar.add(project_root, arcname=project)
            os.replace(tmp_path, final_path)
            return final_path
        except OSError as exc:
            last_exc = exc
            tmp_path.unlink(missing_ok=True)
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
    raise BackupError(
        f"backup of {project!r} failed after {_MAX_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc
