"""Cutover: archive migrated-source originals, never delete them (spec §7.1 steps 6-7)."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.pdata.init_paths import (
    MIGRATED_ARCHIVE_DIRNAME,
    MIGRATED_MANIFEST_FILENAME,
)
from cc_session_tools.lib.pdata.manifest import ManifestEntry


_POINTER_BODY_PREFIX = "This file's data now lives in pdata record group"
_SUBSTITUTE_SUFFIX = ".pdata-pointer.md"


def _pointer_heading(entry: ManifestEntry) -> str:
    return f"# {Path(entry.path).name} — migrated to pdata"


def _pointer_file_content(*, project: str, entry: ManifestEntry) -> str:
    """Generic pointer content (spec pdata/init-pointer-files): the record_group now
    holding this file's data, its field/schema table, and an example query — plus,
    when the entry carries preface_text (set by hand per pm-pdata-do-init's
    garbled-CSV-header fix recipe), that text verbatim under its own heading."""
    lines = [
        _pointer_heading(entry),
        "",
        f"{_POINTER_BODY_PREFIX} `{entry.db_group()}` for "
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


@dataclass(frozen=True)
class PointerPlan:
    """Where one entry's pointer file goes. `pointer_path` is relative to the project root, or
    None when the pointer is skipped; `occupied` lists the candidate paths that were found
    occupied (empty when the pointer goes to its primary `.md` path)."""

    entry_path: str
    pointer_path: str | None
    occupied: tuple[str, ...]

    @property
    def substituted(self) -> bool:
        return self.pointer_path is not None and bool(self.occupied)

    @property
    def skipped(self) -> bool:
        return self.pointer_path is None


def _is_own_pointer(path: Path, entry: ManifestEntry) -> bool:
    """True iff `path` is a regular file (not a symlink) whose first three lines are this
    entry's generated pointer: heading, blank line, and the body line. Tolerates a BOM and CRLF.
    Never raises: anything unreadable is simply not this entry's pointer."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with path.open("rb") as fh:
            head = fh.read(4096)
    except OSError:
        return False
    text = head.decode("utf-8", errors="replace").removeprefix("\ufeff").replace("\r\n", "\n")
    lines = text.split("\n")
    return (
        len(lines) >= 3
        and lines[0] == _pointer_heading(entry)
        and lines[1] == ""
        and lines[2].startswith(_POINTER_BODY_PREFIX)
    )


def plan_pointers(project_root: Path, entries: list[ManifestEntry]) -> list[PointerPlan]:
    """Decide, without touching anything, where each entry's pointer file goes. Entries are
    simulated in order, so a pointer claimed by an earlier entry counts as occupied for later
    ones - the dry-run report and the real cutover therefore agree. An entry's own source path
    counts as vacated (the cutover moves it away before its pointer is written)."""
    claimed: set[str] = set()
    plans: list[PointerPlan] = []
    for entry in entries:
        primary = Path(entry.path).with_suffix(".md").as_posix()
        substitute = (Path(entry.path).parent / f"{Path(entry.path).name}{_SUBSTITUTE_SUFFIX}").as_posix()
        occupied: list[str] = []
        chosen: str | None = None
        for candidate in (primary, substitute):
            on_disk = project_root / candidate
            usable = candidate not in claimed and (
                candidate == entry.path
                or not os.path.lexists(on_disk)
                or _is_own_pointer(on_disk, entry)
            )
            if usable:
                chosen = candidate
                break
            occupied.append(candidate)
        if chosen is not None:
            claimed.add(chosen)
        plans.append(PointerPlan(entry.path, chosen, tuple(occupied)))
    return plans


def _write_pointer_file(
    *, project_root: Path, project: str, entry: ManifestEntry, plan: PointerPlan,
) -> bool:
    """Write the planned pointer. False when the target appeared after planning (skip, never
    overwrite). A target that is the entry's own earlier pointer is rewritten in place."""
    assert plan.pointer_path is not None
    pointer_path = project_root / plan.pointer_path
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    content = _pointer_file_content(project=project, entry=entry)
    if os.path.lexists(pointer_path):
        if not _is_own_pointer(pointer_path, entry):
            return False
        pointer_path.write_text(content, encoding="utf-8")
        return True
    try:
        with pointer_path.open("x", encoding="utf-8") as fh:
            fh.write(content)
    except FileExistsError:
        return False
    return True


def _log_suffix(plan: PointerPlan) -> str:
    if plan.skipped:
        return f"; pointer skipped, {' and '.join(plan.occupied)} already exist(s) and were left untouched"
    if plan.substituted:
        return (
            f"; pointer substituted to {plan.pointer_path} "
            f"({', '.join(plan.occupied)} already exists and was left untouched)"
        )
    return ""


def archive_entries(
    *, project_root: Path, entries: list[ManifestEntry], project: str,
    write_pointer_files: bool = True,
    on_archived: Callable[[ManifestEntry], None] | None = None,
    on_pointer: Callable[[PointerPlan], None] | None = None,
) -> list[PointerPlan]:
    """Move every db-owned entry's source file into project_root/.pdata-migrated/,
    preserving its relative path, and append one line per entry to MANIFEST.md.
    Never deletes — cutover only relocates within project_root (spec §7.1 step 6);
    deleting the archive is a manual, human-directed action (step 7).

    By default (write_pointer_files=True, spec pdata/init-pointer-files), also writes a
    Markdown pointer file back at each entry's original path so anything else in the
    project that cites the old path by name finds a live signpost instead of a silent
    404. It never overwrites an existing file: an occupied `.md` path sends the pointer to
    `<name>.pdata-pointer.md`, and if that is occupied too the pointer is skipped. Pass
    write_pointer_files=False (--leave-no-pointer-files at the CLI) to suppress pointers.
    Returns the final outcome per entry (empty when pointers are suppressed).

    `on_archived`, if given, is called with each entry immediately after its source has been
    moved (before its log line and pointer file are written), so a caller that sees this
    function raise part-way knows exactly which entries were already cut over. `on_pointer` is
    called with each entry's outcome that is a substitution or a skip."""
    if not entries:
        return []
    plans = plan_pointers(project_root, entries) if write_pointer_files else []
    archive_root = project_root / MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest_path = archive_root / MIGRATED_MANIFEST_FILENAME
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    outcomes: list[PointerPlan] = []
    with manifest_path.open("a", encoding="utf-8") as log:
        for index, entry in enumerate(entries):
            source = project_root / entry.path
            destination = archive_root / entry.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
            if on_archived is not None:
                on_archived(entry)
            plan = plans[index] if write_pointer_files else None
            log.write(
                f"- {now} — {entry.path} — migrated source, superseded by ccst pdata "
                f"(record_group={entry.db_group()})"
                f"{_log_suffix(plan) if plan is not None else ''}\n"
            )
            if plan is None:
                continue
            if plan.pointer_path is not None and not _write_pointer_file(
                project_root=project_root, project=project, entry=entry, plan=plan,
            ):
                plan = PointerPlan(entry.path, None, plan.occupied + (plan.pointer_path,))
                log.write(f"  - pointer skipped for {entry.path}: {plan.occupied[-1]} appeared and was left untouched\n")
            outcomes.append(plan)
            if on_pointer is not None and (plan.substituted or plan.skipped):
                on_pointer(plan)
    return outcomes
