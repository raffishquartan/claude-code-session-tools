"""Surface/reap (§9.3): turn the catch-up ledger entries newer than this
session's cursor into digest JobReports, then advance the cursor. Per-session by
design — each session has its own cursor; cross-session dedup is a non-goal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from cc_session_tools.lib.scheduler import cursor, ledger, registry
from cc_session_tools.lib.scheduler.digest import JobReport, Outcome

# Ledger events that produce a digest line.
_RAN_EVENTS = {ledger.LedgerEvent.RUN.value, ledger.LedgerEvent.BACKFILL.value}
_FAIL_EVENTS = {ledger.LedgerEvent.FAIL.value}
_LAUNCH_EVENTS = {ledger.LedgerEvent.LAUNCH.value}


@dataclass(frozen=True, slots=True)
class SurfaceResult:
    reports: list[JobReport]


def _surface_flag(job_id: str, surface_by_id: dict[str, bool]) -> bool:
    return surface_by_id.get(job_id, True)


def surface(*, session_uuid: str) -> SurfaceResult:
    offset = cursor.read_cursor(session_uuid)
    entries, new_offset = ledger.read_since(offset)
    surface_by_id = {s.job_id: s.surface for s in registry.load_registry()}

    reports: list[JobReport] = []
    for e in entries:
        event = str(e.get("event", ""))
        job_id = str(e.get("job_id", ""))
        if event in _FAIL_EVENTS:
            # consecutive_failures is taken as 1 — the ledger entry does not
            # carry the running count, and reading state.json from the reap
            # phase would couple I/O layers. The digest "(1st consecutive)"
            # is conservative and correct for the single visible failure.
            reports.append(JobReport(
                job_id=job_id, outcome=Outcome.FAILED,
                surface=_surface_flag(job_id, surface_by_id), overdue="",
                ran=0, deferred=0, expired=0, consecutive_failures=1,
            ))
        elif event in _RAN_EVENTS:
            reports.append(JobReport(
                job_id=job_id, outcome=Outcome.RAN,
                surface=_surface_flag(job_id, surface_by_id), overdue="",
                ran=int(cast(int, e.get("ran", 0)) or 0), deferred=0, expired=0,
                consecutive_failures=0,
            ))
        elif event in _LAUNCH_EVENTS:
            reports.append(JobReport(
                job_id=job_id, outcome=Outcome.LAUNCHED,
                surface=_surface_flag(job_id, surface_by_id), overdue="",
                ran=0, deferred=0, expired=0, consecutive_failures=0,
            ))
        # skip_expired and defer events are not surfaced as standalone lines.

    cursor.write_cursor(session_uuid, new_offset)
    return SurfaceResult(reports=reports)
