"""Flat-to-nested folder restructuring for `ccst pdata reorganize` (design spec
2026-08-21-pm-project-layout-reference-design.md, §3). Scoped to exactly one operation:
splitting a flat folder into year or year/month subfolders once it's grown past the
`pm-project-layout-reference` skill's 500-file guidance - not general-purpose reorganisation.

dry_run() never touches the filesystem or the database; write() (a later task) performs the
moves and DB updates dry_run() planned, reusing backup.create_backup() as its safety net
exactly the way `ccst pdata init --write` does.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.pdata import init_paths, service
from cc_session_tools.lib.pdata.service import Record

_LEADING_DATE_RE = re.compile(r"^(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})-")

_STRATEGIES = frozenset({"by-year", "by-year-month"})


@dataclass(frozen=True)
class Move:
    old_relative: str
    new_relative: str


@dataclass(frozen=True)
class ExternalReference:
    file: Path
    line_number: int
    line_text: str


@dataclass(frozen=True)
class ReorganizePlan:
    project: str
    project_root: Path
    folder: str
    strategy: str
    moves: list[Move]
    matched_records: list[tuple[Record, str]]  # (record, new_file_path)
    external_references: list[ExternalReference]


def _year_for(path: Path) -> str:
    match = _LEADING_DATE_RE.match(path.name)
    if match:
        return match.group("year")
    return time.strftime("%Y", time.localtime(path.stat().st_mtime))


def _year_month_for(path: Path) -> str:
    match = _LEADING_DATE_RE.match(path.name)
    if match:
        return f"{match.group('year')}/{match.group('month')}"
    return time.strftime("%Y/%m", time.localtime(path.stat().st_mtime))


def _new_relative(folder: str, entry: Path, strategy: str) -> str:
    subdir = _year_for(entry) if strategy == "by-year" else _year_month_for(entry)
    return f"{folder}/{subdir}/{entry.name}"


def _scan_external_references(
    *, project_root: Path, folder: str, old_relatives: list[str],
) -> list[ExternalReference]:
    """Grep every text file in project_root (excluding folder itself and the usual
    ccst/git bookkeeping dirs) for a literal occurrence of any old_relatives entry - reported
    only, never edited (design spec §3: rewriting prose isn't safe to automate).

    folder is excluded by path-prefix comparison, not single-component name matching - folder
    can itself be multi-segment (e.g. "workstreams/ws-01"), which a plain
    `part in excluded_names` check would never match against any single path component.

    Reuses init_paths.EXCLUDED_DIR_NAMES - the same set classify.py's own directory walk
    already excludes - rather than hardcoding a second, incomplete copy: a hardcoded
    ".pdata-migrated" literal here would also miss REHEARSAL_DB_DIRNAME/
    REHEARSAL_BACKUP_DIRNAME, walking into a rehearsal sandbox's own contents on any project
    that has ever used --rehearse and reporting spurious matches from inside it."""
    excluded_dir_names = init_paths.EXCLUDED_DIR_NAMES
    folder_parts = Path(folder).parts
    refs: list[ExternalReference] = []
    for candidate in sorted(project_root.rglob("*")):
        if not candidate.is_file():
            continue
        rel_parts = candidate.relative_to(project_root).parts
        if rel_parts[:len(folder_parts)] == folder_parts:
            continue  # inside the folder being reorganized itself
        if any(part in excluded_dir_names for part in rel_parts[:-1]):
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for old_relative in old_relatives:
                if old_relative in line:
                    refs.append(ExternalReference(
                        file=candidate, line_number=line_number, line_text=line.strip(),
                    ))
    return refs


def _validate_relative_folder(folder: str) -> None:
    """Same boundary guard as service._validate_relative_file_path, applied to --folder:
    without it, an absolute path silently discards project_root entirely when joined with `/`
    (pathlib's own behaviour, not a bug in this code - `Path("/a") / "/etc"` is `Path("/etc")`),
    and a '..' segment can escape project_root the same way a crafted --file could."""
    if folder.startswith("/"):
        raise ValueError(f"--folder must be relative to the project root, got absolute path: {folder!r}")
    if any(segment == ".." for segment in folder.split("/")):
        raise ValueError(f"--folder must not contain '..' path-traversal segments: {folder!r}")


def dry_run(*, project: str, project_root: Path, folder: str, strategy: str) -> ReorganizePlan:
    if strategy not in _STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r} - must be one of {sorted(_STRATEGIES)}")
    _validate_relative_folder(folder)
    folder_path = project_root / folder
    if not folder_path.is_dir():
        raise FileNotFoundError(f"no such folder to reorganize: {folder_path}")

    entries = sorted(p for p in folder_path.iterdir() if p.is_file())
    moves = [
        Move(
            old_relative=f"{folder}/{entry.name}",
            new_relative=_new_relative(folder, entry, strategy),
        )
        for entry in entries
    ]

    matched = service.find_records_by_file_path_prefix(project=project, prefix=f"{folder}/")
    move_by_old = {m.old_relative: m.new_relative for m in moves}
    matched_records = [
        (record, move_by_old[record.file_path])
        for record in matched
        if record.file_path is not None and record.file_path in move_by_old
    ]

    external_references = _scan_external_references(
        project_root=project_root, folder=folder,
        old_relatives=[m.old_relative for m in moves],
    )

    return ReorganizePlan(
        project=project, project_root=project_root, folder=folder, strategy=strategy,
        moves=moves, matched_records=matched_records, external_references=external_references,
    )
