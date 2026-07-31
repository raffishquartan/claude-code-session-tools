"""Cutover: archive migrated-source originals, never delete them (spec §7.1 steps 6-7)."""
from __future__ import annotations

import time
from pathlib import Path

from cc_session_tools.lib.pdata.init_paths import (
    MIGRATED_ARCHIVE_DIRNAME,
    MIGRATED_MANIFEST_FILENAME,
)
from cc_session_tools.lib.pdata.manifest import ManifestEntry


def archive_entries(*, project_root: Path, entries: list[ManifestEntry]) -> None:
    """Move every db-owned entry's source file into project_root/.pdata-migrated/,
    preserving its relative path, and append one line per entry to MANIFEST.md.
    Never deletes — cutover only relocates within project_root (spec §7.1 step 6);
    deleting the archive is a manual, human-directed action (step 7)."""
    if not entries:
        return
    archive_root = project_root / MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest_path = archive_root / MIGRATED_MANIFEST_FILENAME
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with manifest_path.open("a", encoding="utf-8") as log:
        for entry in entries:
            source = project_root / entry.path
            destination = archive_root / entry.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            log.write(
                f"- {now} — {entry.path} — migrated source, superseded by ccst pdata "
                f"(record_group={entry.db_group()})\n"
            )
