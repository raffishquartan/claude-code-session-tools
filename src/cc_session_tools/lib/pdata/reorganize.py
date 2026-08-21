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
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.pdata import backup, init_paths, service
from cc_session_tools.lib.pdata.init_paths import PROPOSAL_FILENAME
from cc_session_tools.lib.pdata.service import Record
from cc_session_tools.lib.pdata.write_log import LOG_FILENAME as WRITE_LOG_FILENAME

_LEADING_DATE_RE = re.compile(r"^(?P<year>\d{4})\.(?P<month>\d{2})\.(?P<day>\d{2})-")

_STRATEGIES = frozenset({"by-year", "by-year-month"})

# Filenames excluded from the external-reference scan by name, not just by directory - both
# persist at the project root indefinitely (classify.py excludes them from classification for
# the same reason) and literally contain the file_path of every classified entry, including
# files under whatever folder is being reorganized. Without this, any project that has ever
# run `ccst pdata init` would get a spurious "external reference" hit inside its own
# bookkeeping every time dry_run() runs.
_EXCLUDED_FILENAMES = frozenset({PROPOSAL_FILENAME, WRITE_LOG_FILENAME})


@dataclass(frozen=True, slots=True)
class Move:
    old_relative: str
    new_relative: str


@dataclass(frozen=True, slots=True)
class ExternalReference:
    file: Path
    line_number: int
    line_text: str


@dataclass(frozen=True, slots=True)
class MatchedRecord:
    record: Record
    new_file_path: str


@dataclass(frozen=True, slots=True)
class ReorganizePlan:
    project: str
    project_root: Path
    folder: str
    strategy: str
    moves: list[Move]
    matched_records: list[MatchedRecord]
    external_references: list[ExternalReference]


def _year_and_month_for(path: Path) -> tuple[str, str]:
    """(year, month) for path - from a leading YYYY.MM.DD- date in the filename if present
    (this codebase's own naming convention, e.g. archive-correspondence's output), else the
    file's mtime. Shared by _year_for/_year_month_for so the date-vs-mtime decision and the
    regex match happen exactly once, not twice with the same logic duplicated between them.

    No validation that a regex-matched month is actually 01-12 (or day 01-31) - a malformed
    but shape-matching date in a filename (e.g. "2025.13.99-foo.md") is accepted at face value.
    Low-risk for a dry-run/planning tool whose output is reviewed before any --write, but worth
    knowing if this is ever reused somewhere the plan isn't reviewed first."""
    match = _LEADING_DATE_RE.match(path.name)
    if match:
        return match.group("year"), match.group("month")
    localtime = time.localtime(path.stat().st_mtime)
    return time.strftime("%Y", localtime), time.strftime("%m", localtime)


def _year_for(path: Path) -> str:
    year, _month = _year_and_month_for(path)
    return year


def _year_month_for(path: Path) -> str:
    year, month = _year_and_month_for(path)
    return f"{year}/{month}"


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
        if candidate.name in _EXCLUDED_FILENAMES:
            continue  # ccst's own bookkeeping - see _EXCLUDED_FILENAMES's comment
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
        MatchedRecord(record=record, new_file_path=move_by_old[record.file_path])
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


@dataclass(frozen=True, slots=True)
class ReorganizeFailure:
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class ReorganizeResult:
    plan: ReorganizePlan
    backup_path: Path | None
    failure: ReorganizeFailure | None


def _is_git_repo(project_root: Path) -> bool:
    return (project_root / ".git").exists()


def _remove_empty_dirs_up_to(*, start: Path, boundary: Path) -> None:
    """Remove start and each successive empty parent directory, stopping at (never removing)
    boundary itself. Shared by _move_file's own failure cleanup and _move_file_back's rollback
    cleanup - both need to walk back up through however many nesting levels by-year-month left
    (two - correspondence/2025/06/) without ever deleting the folder being reorganized."""
    current = start
    while current != boundary and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


def _first_missing_ancestor(*, start: Path, boundary: Path) -> Path | None:
    """Walking from start up towards (not including) boundary, the shallowest directory that
    does not yet exist - exactly the topmost directory `start.mkdir(parents=True)` would
    actually create. None if start already exists (mkdir would be a complete no-op).

    Used so _move_file's failure-cleanup only ever removes directories its own mkdir call
    genuinely created, never one that already existed before it - e.g. left over from an
    earlier partial/crashed run, or present for an unrelated reason. Without this, cleanup
    would delete any empty directory sitting at the destination path regardless of whether
    this call was the one that created it."""
    if start.exists():
        return None
    missing = start
    while missing.parent != boundary and not missing.parent.exists():
        missing = missing.parent
    return missing


def _move_file(*, project_root: Path, move: Move, folder: str, use_git: bool) -> None:
    src = project_root / move.old_relative
    dest = project_root / move.new_relative
    folder_path = project_root / folder
    newly_created_root = _first_missing_ancestor(start=dest.parent, boundary=folder_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if use_git:
            subprocess.run(
                ["git", "mv", move.old_relative, move.new_relative],
                cwd=project_root, check=True, capture_output=True, text=True,
            )
        else:
            src.rename(dest)
    except (OSError, subprocess.CalledProcessError):
        # The mkdir above may have just created dest.parent (and its own new parents, for
        # by-year-month) - if the move itself then failed, that directory was never populated
        # and would otherwise be left behind as debris: write()'s own rollback only knows
        # about *completed* moves (this one was never added to `moved`), so nothing else
        # would ever clean it up. newly_created_root is None when dest.parent already existed
        # before this call (nothing to clean up - it wasn't ours to remove).
        if newly_created_root is not None:
            _remove_empty_dirs_up_to(start=dest.parent, boundary=newly_created_root.parent)
        raise


def _move_file_back(*, project_root: Path, move: Move, folder: str, use_git: bool) -> None:
    """Undo _move_file - used by write()'s rollback path only."""
    dest_parent = (project_root / move.new_relative).parent
    if use_git:
        subprocess.run(
            ["git", "mv", move.new_relative, move.old_relative],
            cwd=project_root, check=True, capture_output=True, text=True,
        )
    else:
        (project_root / move.new_relative).rename(project_root / move.old_relative)
    _remove_empty_dirs_up_to(start=dest_parent, boundary=project_root / folder)


def write(*, project: str, project_root: Path, folder: str, strategy: str) -> ReorganizeResult:
    plan = dry_run(project=project, project_root=project_root, folder=folder, strategy=strategy)
    use_git = _is_git_repo(project_root)

    # Matches init_service.write()'s own handling of this exact call (init_service.py
    # ~lines 220-232): create_backup() can exhaust its retries and raise BackupError, which
    # must become a structured failure here too, not an uncaught crash before anything has
    # been moved.
    try:
        backup_path = backup.create_backup(project=project, project_root=project_root)
    except backup.BackupError as exc:
        return ReorganizeResult(plan=plan, backup_path=None, failure=ReorganizeFailure(reasons=[str(exc)]))

    moved: list[Move] = []
    # (matched, post-update Record incl. its bumped version) for every update_record() call
    # that succeeded so far - needed to reverse them in the right order on failure, each with
    # the expected_version its OWN update actually left the row at, not its pre-update version.
    updated: list[tuple[MatchedRecord, Record]] = []
    try:
        for move in plan.moves:
            _move_file(project_root=project_root, move=move, folder=folder, use_git=use_git)
            moved.append(move)

        for matched in plan.matched_records:
            updated_record = service.update_record(
                project=project, record_id=matched.record.id,
                expected_version=matched.record.version,
                content=None, file_path=matched.new_file_path, fields={},
            )
            updated.append((matched, updated_record))
    except (OSError, subprocess.CalledProcessError, service.VersionConflictError,
            service.RecordNotFoundError) as exc:
        reasons = [str(exc)]
        # Reverse successfully-applied DB updates before moving files back - matched.record
        # is filtered upstream (dry_run) to always have a non-None file_path, so this always
        # restores a real prior value, never a no-op None. Each reversal is wrapped
        # individually, matching init_service._rollback()'s own contract: an unwrapped raise
        # here would abort the rest of the rollback (and this whole function) instead of
        # returning a ReorganizeResult at all.
        unreverted_new_paths: set[str] = set()
        for matched, updated_record in reversed(updated):
            try:
                service.update_record(
                    project=project, record_id=matched.record.id,
                    expected_version=updated_record.version,
                    content=None, file_path=matched.record.file_path, fields={},
                )
            except (service.VersionConflictError, service.RecordNotFoundError) as rollback_exc:
                # This record's file_path couldn't be reverted - leave its file at the new
                # location too, rather than moving the file back while the DB still points at
                # the new path (which would recreate the exact dangling-reference state this
                # rollback exists to avoid).
                unreverted_new_paths.add(matched.new_file_path)
                reasons.append(f"record {matched.record.id}: rollback failed: {rollback_exc}")

        for move in reversed(moved):
            if move.new_relative in unreverted_new_paths:
                continue
            try:
                _move_file_back(project_root=project_root, move=move, folder=folder, use_git=use_git)
            except (OSError, subprocess.CalledProcessError) as rollback_exc:
                reasons.append(f"{move.new_relative}: rollback failed: {rollback_exc}")

        return ReorganizeResult(
            plan=plan, backup_path=backup_path,
            failure=ReorganizeFailure(reasons=reasons),
        )

    return ReorganizeResult(plan=plan, backup_path=backup_path, failure=None)
