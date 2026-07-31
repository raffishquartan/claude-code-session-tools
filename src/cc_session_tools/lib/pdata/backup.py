"""Pre-cutover backup snapshot for `ccst pdata init` (spec §7.1 step 5)."""
from __future__ import annotations

import os
import tarfile
import time
from pathlib import Path

from cc_session_tools.lib import paths

BACKUP_DIR_ENV = "CCST_PDATA_BACKUP_DIR"


def backup_dir() -> Path:
    override = os.environ.get(BACKUP_DIR_ENV)
    return Path(override).expanduser() if override else paths.data_home() / "pdata-backups"


def create_backup(*, project: str, project_root: Path) -> Path:
    """tar.gz snapshot of project_root as it stands right now, written outside
    project_root (spec §7.1 step 5 — "stored outside the project folder, before
    touching any original file"). One backup per --write invocation; never
    overwritten or extended incrementally, matching the big-bang migration
    philosophy (spec goals)."""
    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    tar_path = target_dir / f"{project}-{int(time.time())}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(project_root, arcname=project)
    return tar_path
