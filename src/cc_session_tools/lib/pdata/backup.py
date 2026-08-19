"""Pre-cutover backup snapshot for `ccst pdata init` (spec §7.1 step 5)."""
from __future__ import annotations

import os
import tarfile
import time
from pathlib import Path

from cc_session_tools.lib import paths

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
