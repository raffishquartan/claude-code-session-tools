"""Surface/reap (§9.3): turn the catch-up ledger entries newer than this
session's cursor into digest JobReports, then advance the cursor. Per-session by
design — each session has its own cursor; cross-session dedup is a non-goal.

A session that has been dormant for a long time can accumulate a large backlog
of routine LAUNCH/RUN events. Replaying those individually would dump the
entire backlog into the digest as if it just happened, so a large or stale
backlog of routine events is folded into a single summary line instead
(§ staleness fix). FAILED/SUSPENDED events are never summarised — they always
replay in full, each carrying an explicit relative-age suffix so a long-past
failure is never mistaken for something currently happening.

A completed run (RUN/BACKFILL) is always turned into a visible JobReport,
regardless of the job's `surface` flag — only a LAUNCH ("started", not "ran")
still respects it (§ visibility fix). RAN/BACKFILL reports carry `surface=True`
unconditionally rather than the per-job flag, since digest.py never consults
`surface` for Outcome.RAN — the per-job flag is only meaningful for LAUNCH,
where it is still looked up via `_surface_flag`.

A clean (0-exit) run's captured stdout coalesces per job_id the same way bare
routine runs do: several such runs for the same job within one digest render
as one line carrying a count, the span they cover, and only the MOST RECENT
run's output (§ output-coalesce fix) — see `_output_reports`. Findings
(nonzero-exit captured stdout) are exempt, same as FAILED/SUSPENDED: they are
signal, never folded, always replayed individually with their own age suffix.

`surface(..., lookback=...)` optionally widens the read below the session's
own cursor to also pick up ledger activity within `lookback` of `now`, for the
SessionStart 24h rolling lookback (§ widen fix) — see catchup.py, the only
caller that ever passes it."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import cast

from cc_session_tools.lib.scheduler import cursor, ledger, registry
from cc_session_tools.lib.scheduler.digest import JobReport, Outcome

# Ledger events that produce a digest line.
_BACKFILL_EVENTS = {ledger.LedgerEvent.BACKFILL.value}
_RUN_EVENTS = {ledger.LedgerEvent.RUN.value}
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


def _format_span(oldest: datetime, now: datetime) -> str:
    """Same buckets as `_format_age`, but as a window ("11h") rather than a
    point in time ("11h ago") - for the "N times in last {span}" wording on
    coalesced clean-output runs."""
    minutes = max(1, int((now - oldest).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


# Sort key for an entry whose ts is missing/unparseable - sorts first, same
# convention as `_format_age`'s "unknown time ago" treating it as maximally old.
_UNKNOWN_TS = datetime.min.replace(tzinfo=timezone.utc)


def _individual_routine_report(e: dict[str, object], *, surface: bool, now: datetime) -> JobReport:
    event = str(e.get("event", ""))
    job_id = str(e.get("job_id", ""))
    age = _format_age(_parse_ts(e.get("ts")), now)
    if event in _LAUNCH_EVENTS:
        return JobReport(
            job_id=job_id, outcome=Outcome.LAUNCHED, surface=surface, overdue="",
            ran=0, deferred=0, expired=0, consecutive_failures=0, age=age,
        )
    return JobReport(
        job_id=job_id, outcome=Outcome.RAN, surface=surface, overdue="",
        ran=int(cast(int, e.get("ran", 0)) or 0), deferred=0, expired=0,
        consecutive_failures=0, age=age,
    )


def _routine_reports(
    entries: list[dict[str, object]], *, now: datetime,
) -> list[tuple[datetime, JobReport]]:
    """Surfaced (non-silent) LAUNCH/RUN entries only, paired with a sort
    timestamp so the caller can interleave them chronologically with every
    other report type. Folds a large or stale backlog into one summary
    JobReport (unchanged). Otherwise, entries for the SAME job_id and the
    same kind (LAUNCH vs RAN) coalesce into one line carrying a count (§
    coalesce fix) - a job reconciled by several short-lived sessions within
    one sweep must not replay as N near-identical lines. A group of exactly
    one entry renders exactly as before (no '×1' suffix)."""
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
        sort_ts = oldest if oldest is not None else _UNKNOWN_TS
        return [(sort_ts, JobReport(
            job_id="", outcome=Outcome.SUMMARY, surface=True, overdue="",
            ran=0, deferred=0, expired=0, consecutive_failures=0,
            age=age, count=len(entries),
        ))]

    groups: dict[tuple[str, bool], list[dict[str, object]]] = {}
    for e in entries:
        job_id = str(e.get("job_id", ""))
        is_launch = str(e.get("event", "")) in _LAUNCH_EVENTS
        groups.setdefault((job_id, is_launch), []).append(e)

    out: list[tuple[datetime, JobReport]] = []
    for (job_id, is_launch), group in groups.items():
        timestamps = [_parse_ts(g.get("ts")) for g in group]
        latest = max((t for t in timestamps if t is not None), default=None)
        sort_ts = latest if latest is not None else _UNKNOWN_TS
        if len(group) == 1:
            out.append((sort_ts, _individual_routine_report(group[0], surface=True, now=now)))
            continue
        age = _format_age(latest, now)
        outcome = Outcome.LAUNCHED if is_launch else Outcome.RAN
        ran = 0 if is_launch else sum(int(cast(int, g.get("ran", 0)) or 0) for g in group)
        out.append((sort_ts, JobReport(
            job_id=job_id, outcome=outcome, surface=True, overdue="",
            ran=ran, deferred=0, expired=0, consecutive_failures=0,
            age=age, count=len(group),
        )))
    return out


def _output_reports(
    entries: list[dict[str, object]], *, now: datetime,
) -> list[tuple[datetime, JobReport]]:
    """Clean-output (0-exit, captured stdout) RUN/BACKFILL entries, grouped by
    job_id. A job_id with only one entry renders exactly as before (no count
    suffix). A job_id with several entries within one digest coalesces to one
    line carrying a count, the span the group covers, and only the MOST
    RECENT entry's output - not N near-identical output blocks for the same
    job (§ output-coalesce fix). Findings-bearing (nonzero-exit) entries never
    reach this function - they are signal, not routine noise, and always
    replay individually (see the caller)."""
    groups: dict[str, list[dict[str, object]]] = {}
    for e in entries:
        groups.setdefault(str(e.get("job_id", "")), []).append(e)

    out: list[tuple[datetime, JobReport]] = []
    for job_id, group in groups.items():
        timestamps = [_parse_ts(g.get("ts")) for g in group]
        latest = max((t for t in timestamps if t is not None), default=None)
        oldest = min((t for t in timestamps if t is not None), default=None)
        sort_ts = latest if latest is not None else _UNKNOWN_TS
        if len(group) == 1:
            e = group[0]
            out.append((sort_ts, JobReport(
                job_id=job_id, outcome=Outcome.RAN, surface=True, overdue="",
                ran=int(cast(int, e.get("ran", 0)) or 0), deferred=0, expired=0,
                consecutive_failures=0, age=_format_age(latest, now),
                output=str(e.get("error")),
            )))
            continue
        # Most recent entry by ts (fall back to list order for unparseable ts).
        latest_entry = max(
            group, key=lambda g: (_parse_ts(g.get("ts")) or _UNKNOWN_TS),
        )
        window = _format_span(oldest, now) if oldest is not None else "unknown time"
        out.append((sort_ts, JobReport(
            job_id=job_id, outcome=Outcome.RAN, surface=True, overdue="",
            ran=sum(int(cast(int, g.get("ran", 0)) or 0) for g in group),
            deferred=0, expired=0, consecutive_failures=0,
            age=_format_age(latest, now), count=len(group),
            output=str(latest_entry.get("error")), window=window,
        )))
    return out


def surface(
    *, session_uuid: str, now: datetime, lookback: timedelta | None = None,
) -> SurfaceResult:
    cursor_offset = cursor.read_cursor(session_uuid)
    if lookback is not None:
        # Widen the read below the session's own cursor to also pick up
        # activity within `lookback` of `now`, even if it predates this
        # session's seeded cursor. A single merged read (rather than two
        # separate queries) means an entry reachable via both paths cannot
        # be surfaced twice within this call.
        floor_offset = ledger.offset_before_ts((now - lookback).strftime(_TS_FMT))
        start_offset = min(cursor_offset, floor_offset)
    else:
        start_offset = cursor_offset
    entries, new_offset = ledger.read_since(start_offset)
    surface_by_id = {s.job_id: s.surface for s in registry.load_registry()}

    # (sort timestamp, report) pairs, sorted once at the end so every report
    # type - individually-appended and routine/coalesced/folded alike -
    # interleaves in the order things actually happened (§ chronological
    # order fix), not in the order surface()'s branches happen to process
    # them.
    pairs: list[tuple[datetime, JobReport]] = []
    routine: list[dict[str, object]] = []
    clean_output: list[dict[str, object]] = []
    for e in entries:
        event = str(e.get("event", ""))
        job_id = str(e.get("job_id", ""))
        sent = _parse_ts(e.get("ts"))
        sort_ts = sent if sent is not None else _UNKNOWN_TS
        if event in _FAIL_EVENTS:
            raw_cf = e.get("consecutive_failures")
            consecutive = int(raw_cf) if isinstance(raw_cf, int) else 1
            pairs.append((sort_ts, JobReport(
                job_id=job_id, outcome=Outcome.FAILED,
                surface=_surface_flag(job_id, surface_by_id), overdue="",
                ran=0, deferred=0, expired=0, consecutive_failures=consecutive,
                age=_format_age(sent, now),
            )))
        elif event in _LAUNCH_EVENTS:
            # LAUNCHED "started" notices are the one outcome that still
            # respects `surface` - unaffected by the RUN/BACKFILL
            # always-visible handling below.
            if _surface_flag(job_id, surface_by_id):
                routine.append(e)
            else:
                pairs.append((sort_ts, _individual_routine_report(e, surface=False, now=now)))
        elif event in _RUN_EVENTS or event in _BACKFILL_EVENTS:
            # A completed run is always visible now - `surface` no longer
            # gates it (§ visibility fix). RUN and BACKFILL (>1 missed
            # interval caught up in one attempt) share this handling: worker.py
            # always captures stdout into the ledger's `error` column for
            # both alike, so BACKFILL must read it too, not drop it (§
            # backfill-output fix).
            raw_exit = e.get("exit_code")
            captured = e.get("error")
            if captured:
                is_warning = raw_exit not in (0, None)
                if is_warning:
                    # Findings are signal, not routine noise - never coalesced,
                    # always replayed individually (see module docstring).
                    # surface=True unconditionally: a completed run is always
                    # visible, so the per-job flag is not load-bearing here.
                    pairs.append((sort_ts, JobReport(
                        job_id=job_id, outcome=Outcome.RAN, surface=True, overdue="",
                        ran=int(cast(int, e.get("ran", 0)) or 0), deferred=0, expired=0,
                        consecutive_failures=0, findings=str(captured),
                        age=_format_age(sent, now),
                    )))
                else:
                    # A clean (0-exit) run's captured stdout - eligible for
                    # per-job_id coalescing (§ output-coalesce fix), same
                    # spirit as the bare-run routine fold below but keeping
                    # the most recent output rather than dropping it.
                    clean_output.append(e)
            else:
                # A bare clean run with no captured stdout - still eligible
                # for the routine backlog fold (spam control, not the
                # surface gate), never dropped as invisible.
                routine.append(e)
        elif event in _SUSPEND_EVENTS:
            raw_cf = e.get("consecutive_failures")
            consecutive = int(raw_cf) if isinstance(raw_cf, int) else 0
            pairs.append((sort_ts, JobReport(
                job_id=job_id, outcome=Outcome.SUSPENDED,
                surface=_surface_flag(job_id, surface_by_id), overdue="",
                ran=0, deferred=0, expired=0, consecutive_failures=consecutive,
                age=_format_age(sent, now),
            )))
        # skip_expired and defer events are not surfaced as standalone lines.

    pairs.extend(_routine_reports(routine, now=now))
    pairs.extend(_output_reports(clean_output, now=now))
    pairs.sort(key=lambda p: p[0])

    cursor.write_cursor(session_uuid, new_offset)
    return SurfaceResult(reports=[report for _, report in pairs])
