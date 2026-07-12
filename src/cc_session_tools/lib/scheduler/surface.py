"""Surface/reap (§9.3): turn the catch-up ledger entries newer than this
session's cursor into digest JobReports, then advance the cursor. Per-session by
design — each session has its own cursor; cross-session dedup is a non-goal.

A session that has been dormant for a long time can accumulate a large backlog
of routine LAUNCH/RUN events. Replaying those individually would dump the
entire backlog into the digest as if it just happened, so a large or stale
backlog of routine events is folded into a single summary line instead
(§ staleness fix). FAILED/SUSPENDED events are never summarised — they always
replay in full, each carrying an explicit relative-age suffix so a long-past
failure is never mistaken for something currently happening."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

from cc_session_tools.lib.scheduler import cursor, ledger, registry
from cc_session_tools.lib.scheduler.digest import JobReport, Outcome

# Ledger events that produce a digest line.
_BACKFILL_EVENTS = {ledger.LedgerEvent.BACKFILL.value}
_ROUTINE_EVENTS = {ledger.LedgerEvent.LAUNCH.value, ledger.LedgerEvent.RUN.value}
_LAUNCH_EVENTS = {ledger.LedgerEvent.LAUNCH.value}
_FAIL_EVENTS = {ledger.LedgerEvent.FAIL.value}
_SUSPEND_EVENTS = {ledger.LedgerEvent.SUSPEND.value}

# A backlog of routine (LAUNCH/RUN) events larger than this, or whose oldest
# entry is older than this, is folded into one summary line rather than
# replayed individually.
_ROUTINE_SUMMARY_COUNT_THRESHOLD = 100
_ROUTINE_SUMMARY_AGE_THRESHOLD = timedelta(hours=48)

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True, slots=True)
class SurfaceResult:
    reports: list[JobReport]


def _surface_flag(job_id: str, surface_by_id: dict[str, bool]) -> bool:
    return surface_by_id.get(job_id, True)


def _parse_ts(ts: object) -> datetime | None:
    """Parse a ledger row's ``ts`` field. Ledger rows are external/on-disk
    data the surfacing layer must not crash on, so an unparseable or missing
    timestamp yields ``None`` rather than raising."""
    if not isinstance(ts, str):
        return None
    try:
        return datetime.strptime(ts, _TS_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _format_age(sent: datetime | None, now: datetime) -> str:
    if sent is None:
        return "unknown time ago"
    minutes = int((now - sent).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _individual_routine_report(e: dict[str, object], *, surface: bool) -> JobReport:
    event = str(e.get("event", ""))
    job_id = str(e.get("job_id", ""))
    if event in _LAUNCH_EVENTS:
        return JobReport(
            job_id=job_id, outcome=Outcome.LAUNCHED, surface=surface, overdue="",
            ran=0, deferred=0, expired=0, consecutive_failures=0,
        )
    return JobReport(
        job_id=job_id, outcome=Outcome.RAN, surface=surface, overdue="",
        ran=int(cast(int, e.get("ran", 0)) or 0), deferred=0, expired=0,
        consecutive_failures=0,
    )


def _routine_reports(entries: list[dict[str, object]], *, now: datetime) -> list[JobReport]:
    """Surfaced (non-silent) LAUNCH/RUN entries only. Folds a large or stale
    backlog into one summary JobReport; otherwise replays each individually,
    exactly as before."""
    if not entries:
        return []
    oldest: datetime | None = None
    saw_unparseable = False
    for e in entries:
        sent = _parse_ts(e.get("ts"))
        if sent is None:
            saw_unparseable = True
            continue
        if oldest is None or sent < oldest:
            oldest = sent
    is_stale = saw_unparseable or (
        oldest is not None and now - oldest >= _ROUTINE_SUMMARY_AGE_THRESHOLD
    )
    if len(entries) > _ROUTINE_SUMMARY_COUNT_THRESHOLD or is_stale:
        age = "unknown time ago" if oldest is None else _format_age(oldest, now)
        return [JobReport(
            job_id="", outcome=Outcome.SUMMARY, surface=True, overdue="",
            ran=0, deferred=0, expired=0, consecutive_failures=0,
            age=age, count=len(entries),
        )]
    return [_individual_routine_report(e, surface=True) for e in entries]


def surface(*, session_uuid: str, now: datetime) -> SurfaceResult:
    offset = cursor.read_cursor(session_uuid)
    entries, new_offset = ledger.read_since(offset)
    surface_by_id = {s.job_id: s.surface for s in registry.load_registry()}

    reports: list[JobReport] = []
    routine: list[dict[str, object]] = []
    for e in entries:
        event = str(e.get("event", ""))
        job_id = str(e.get("job_id", ""))
        if event in _FAIL_EVENTS:
            raw_cf = e.get("consecutive_failures")
            consecutive = int(raw_cf) if isinstance(raw_cf, int) else 1
            reports.append(JobReport(
                job_id=job_id, outcome=Outcome.FAILED,
                surface=_surface_flag(job_id, surface_by_id), overdue="",
                ran=0, deferred=0, expired=0, consecutive_failures=consecutive,
                age=_format_age(_parse_ts(e.get("ts")), now),
            ))
        elif event in _ROUTINE_EVENTS:
            if _surface_flag(job_id, surface_by_id):
                routine.append(e)
            else:
                # Silent jobs never surface, individually or in the summary —
                # keep the report (hidden by digest's surface check) so callers
                # that inspect SurfaceResult.reports directly still see it.
                reports.append(_individual_routine_report(e, surface=False))
        elif event in _BACKFILL_EVENTS:
            reports.append(JobReport(
                job_id=job_id, outcome=Outcome.RAN,
                surface=_surface_flag(job_id, surface_by_id), overdue="",
                ran=int(cast(int, e.get("ran", 0)) or 0), deferred=0, expired=0,
                consecutive_failures=0,
            ))
        elif event in _SUSPEND_EVENTS:
            raw_cf = e.get("consecutive_failures")
            consecutive = int(raw_cf) if isinstance(raw_cf, int) else 0
            reports.append(JobReport(
                job_id=job_id, outcome=Outcome.SUSPENDED,
                surface=_surface_flag(job_id, surface_by_id), overdue="",
                ran=0, deferred=0, expired=0, consecutive_failures=consecutive,
                age=_format_age(_parse_ts(e.get("ts")), now),
            ))
        # skip_expired and defer events are not surfaced as standalone lines.

    reports.extend(_routine_reports(routine, now=now))

    cursor.write_cursor(session_uuid, new_offset)
    return SurfaceResult(reports=reports)
