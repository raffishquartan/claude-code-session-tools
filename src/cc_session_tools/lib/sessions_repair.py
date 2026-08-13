"""Repair sessions.db rows whose project_dir was written as a non-absolute Path.
Resolves each affected row's correct project_dir by locating its on-disk
cc-sessions/<basename>/ directory under the configured roots, then updates the row
in place, preserving its timestamps. Never guesses when a basename's on-disk
location is ambiguous (found under >1 root/project) or missing (found under none)
— those are reported, not silently resolved, because a wrong guess would
silently point `ccl`/`ccs` at the wrong project's session data, which is worse
than the original missing-from-listings bug."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from cc_session_tools.lib import sessions_db


@dataclass(slots=True)
class RepairReport:
    """Mutable accumulator, built up in place by repair() — not a frozen value object.

    Note the duplicate-basename semantics differ across fields: `ambiguous` is a
    dict keyed by basename (so a duplicate basename overwrites its own entry),
    while `repaired`/`unresolved`/`conflicts` are lists (so a duplicate basename
    can appear more than once). A caller counting rows across fields must account
    for this asymmetry."""

    repaired: list[tuple[str, Path]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[Path]] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)


def find_non_absolute_rows(*, path: Path | None = None) -> list[sessions_db.SessionRow]:
    """Every sessions.db row whose project_dir is not an absolute path."""
    return [r for r in sessions_db.list_sessions(path=path) if not r.project_dir.is_absolute()]


def _resolve_on_disk(basename: str, roots: list[Path]) -> list[Path]:
    """Every project directly under a configured root whose cc-sessions/<basename>/
    exists on disk — the set of plausible correct project_dir values for this row."""
    matches: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for proj in root.iterdir():
            if proj.is_dir() and (proj / "cc-sessions" / basename).is_dir():
                matches.append(proj)
    return matches


def repair(
    roots: list[Path], *, path: Path | None = None, dry_run: bool = True
) -> RepairReport:
    """Resolve and (unless dry_run) fix every non-absolute project_dir row.

    dry_run=True (the default) only computes and returns the report. dry_run=False
    updates sessions.db in place — one UPDATE per uniquely-resolved row, leaving
    start_date/last_opened/last_active/discovered_at untouched: a project_dir
    correction is not a new session-activity event, so it must not move
    last_opened/last_active. Callers that want a safety copy first should back up
    the DB file (e.g. via db.backup_to()) before calling with dry_run=False; this
    function does not.

    A resolved row is never blindly UPDATEd: (project_dir, basename) is the table's
    PRIMARY KEY, so if a correct row for (resolved_dir, basename) already exists —
    e.g. ccd.py's ensure_session_row() wrote it independently at session-creation
    time — writing the corrupted row on top would violate that key. Such rows are
    reported as `conflicts` and left untouched rather than raising mid-batch. The
    same applies when two bad rows in the SAME batch resolve to the same target
    (e.g. one row has project_dir='.', another has '..', and both name the same
    basename): applying one would make the other's UPDATE collide with it, so
    both are reported as conflicts rather than letting row-processing order
    arbitrarily pick a "winner".
    """
    bad_rows = find_non_absolute_rows(path=path)
    report = RepairReport()
    if not bad_rows:
        return report

    resolutions: list[tuple[sessions_db.SessionRow, Path]] = []
    for row in bad_rows:
        candidates = _resolve_on_disk(row.basename, roots)
        if len(candidates) == 1:
            resolutions.append((row, candidates[0]))
        elif len(candidates) == 0:
            report.unresolved.append(row.basename)
        else:
            report.ambiguous[row.basename] = candidates

    if not resolutions:
        return report

    # Split out any resolution whose target (new_dir, basename) already has a row —
    # applying it would violate the PRIMARY KEY. Check before writing anything, not
    # via try/except around the UPDATE, so one conflict can't abort sibling rows'
    # otherwise-valid updates. `batch_target_counts` catches the same collision
    # when it happens WITHIN this batch (two bad rows resolving to the same
    # target): every row sharing such a target is a conflict, not just whichever
    # one is processed second — picking a "winner" by iteration order would be
    # arbitrary and non-deterministic.
    existing_keys = {
        (r.project_dir, r.basename) for r in sessions_db.list_sessions(path=path)
    }
    batch_target_counts = Counter((new_dir, row.basename) for row, new_dir in resolutions)
    applyable: list[tuple[sessions_db.SessionRow, Path]] = []
    for row, new_dir in resolutions:
        target = (new_dir, row.basename)
        if target in existing_keys or batch_target_counts[target] > 1:
            report.conflicts.append(row.basename)
        else:
            applyable.append((row, new_dir))
            report.repaired.append((row.basename, new_dir))

    if dry_run or not applyable:
        return report

    conn = sessions_db.connect(path=path)
    try:
        for row, new_dir in applyable:
            conn.execute(
                "UPDATE sessions SET project_dir = ? WHERE project_dir = ? AND basename = ?",
                (str(new_dir), str(row.project_dir), row.basename),
            )
        conn.commit()
    finally:
        conn.close()
    return report
