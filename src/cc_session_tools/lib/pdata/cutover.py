"""Cutover: archive migrated-source originals, never delete them (spec §7.1 steps 6-7)."""
from __future__ import annotations

import time
from pathlib import Path

from cc_session_tools.lib.pdata.init_paths import (
    MIGRATED_ARCHIVE_DIRNAME,
    MIGRATED_MANIFEST_FILENAME,
)
from cc_session_tools.lib.pdata.manifest import ManifestEntry


def _pointer_file_content(*, project: str, entry: ManifestEntry) -> str:
    """Generic pointer content (spec pdata/init-pointer-files): the record_group now
    holding this file's data, its field/schema table, and an example query — plus,
    when the entry carries preface_text (set by hand per pm-pdata-do-init's
    garbled-CSV-header fix recipe), that text verbatim under its own heading."""
    lines = [
        f"# {Path(entry.path).name} — migrated to pdata",
        "",
        f"This file's data now lives in pdata record group `{entry.db_group()}` for "
        f"project `{project}`. The original was moved to "
        f"`{MIGRATED_ARCHIVE_DIRNAME}/{entry.path}` and is no longer read from here.",
        "",
        "## Schema",
        "",
        "| field | sql_type | description |",
        "| --- | --- | --- |",
    ]
    for spec in entry.fields:
        lines.append(f"| {spec.name} | {spec.sql_type} | {spec.description or ''} |")
    lines += [
        "",
        "## Example query",
        "",
        "```sh",
        f"ccst pdata list --project {project} --group {entry.db_group()}",
        "```",
    ]
    if entry.preface_text:
        lines += [
            "",
            "## Preserved preface text (removed from the original file before migration)",
            "",
            entry.preface_text,
        ]
    return "\n".join(lines) + "\n"


def _write_pointer_file(*, project_root: Path, project: str, entry: ManifestEntry) -> None:
    pointer_path = project_root / Path(entry.path).with_suffix(".md")
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(
        _pointer_file_content(project=project, entry=entry), encoding="utf-8",
    )


def archive_entries(
    *, project_root: Path, entries: list[ManifestEntry], project: str,
    write_pointer_files: bool = True,
) -> None:
    """Move every db-owned entry's source file into project_root/.pdata-migrated/,
    preserving its relative path, and append one line per entry to MANIFEST.md.
    Never deletes — cutover only relocates within project_root (spec §7.1 step 6);
    deleting the archive is a manual, human-directed action (step 7).

    By default (write_pointer_files=True, spec pdata/init-pointer-files), also writes a
    Markdown pointer file back at each entry's original path so anything else in the
    project that cites the old path by name finds a live signpost instead of a silent
    404. Pass write_pointer_files=False (--leave-no-pointer-files at the CLI) to
    suppress this."""
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
            if write_pointer_files:
                _write_pointer_file(project_root=project_root, project=project, entry=entry)
