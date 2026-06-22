"""Pure due-computation. Given a cadence, a baseline (last success or
registered_at), and an injected ``now``, enumerate the scheduled instants in
``(baseline, now]`` and split them into in-window instants and an expired count
(older than ``now - catchup_window``). No I/O, no ``datetime.now()`` call.

Wall-clock cadences are computed on the local calendar of the injected ``now``
(one instant per calendar occurrence); see §17.3 for the DST contract."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta

from cc_session_tools.lib.scheduler.cadence import NTH_LAST, Cadence, CadenceKind


@dataclass(frozen=True, slots=True)
class OwedResult:
    instants: list[datetime]
    expired_count: int


def _every_instants(interval: timedelta, baseline: datetime, now: datetime) -> list[datetime]:
    out: list[datetime] = []
    nxt = baseline + interval
    while nxt <= now:
        out.append(nxt)
        nxt = nxt + interval
    return out


def _every_anchored_instants(
    c: Cadence, baseline: datetime, now: datetime
) -> list[datetime]:
    """Drift-free instants on the fixed grid anchor, anchor+interval, … in
    ``(baseline, now]``. The anchor date is taken at local midnight in the tz of
    ``now`` so comparisons stay tz-aware."""
    assert c.interval is not None and c.anchor is not None
    grid = datetime(c.anchor.year, c.anchor.month, c.anchor.day, tzinfo=now.tzinfo)
    out: list[datetime] = []
    cur = grid
    while cur <= now:
        if cur > baseline:
            out.append(cur)
        cur = cur + c.interval
    return out


def _at_local(day: datetime, hour: int, minute: int) -> datetime:
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _daily_instants(c: Cadence, baseline: datetime, now: datetime) -> list[datetime]:
    assert c.hour is not None and c.minute is not None
    out: list[datetime] = []
    cur = _at_local(baseline, c.hour, c.minute)
    if cur <= baseline:
        cur = cur + timedelta(days=1)
    while cur <= now:
        out.append(cur)
        cur = cur + timedelta(days=1)
    return out


def _weekly_instants(c: Cadence, baseline: datetime, now: datetime) -> list[datetime]:
    assert c.hour is not None and c.minute is not None and c.dow is not None
    out: list[datetime] = []
    cur = _at_local(baseline, c.hour, c.minute)
    # Advance to the first matching weekday strictly after baseline.
    while cur.weekday() != c.dow or cur <= baseline:
        cur = cur + timedelta(days=1)
        cur = _at_local(cur, c.hour, c.minute)
    while cur <= now:
        out.append(cur)
        cur = cur + timedelta(days=7)
    return out


def _add_month(dt: datetime) -> datetime:
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    return dt.replace(year=year, month=month)


def _monthly_instants(c: Cadence, baseline: datetime, now: datetime) -> list[datetime]:
    assert c.hour is not None and c.minute is not None and c.dom is not None
    out: list[datetime] = []
    cur = _at_local(baseline.replace(day=1), c.hour, c.minute)
    while cur <= now:
        # Clamp dom to the last day of this month (e.g. 31 in February → 28/29).
        last_day = calendar.monthrange(cur.year, cur.month)[1]
        candidate = cur.replace(day=min(c.dom, last_day))
        if baseline < candidate <= now:
            out.append(candidate)
        cur = _add_month(cur)
    return out


def _nth_weekday_of_month(year: int, month: int, dow: int, nth: int) -> int | None:
    """Day-of-month of the nth (``1..5``) occurrence of ``dow`` in the month, or
    the last occurrence when ``nth == NTH_LAST``. Returns ``None`` if the month
    has fewer than ``nth`` occurrences of that weekday (e.g. a ``#5`` request in a
    month with only four)."""
    days_in_month = calendar.monthrange(year, month)[1]
    matches = [d for d in range(1, days_in_month + 1)
               if datetime(year, month, d).weekday() == dow]
    if nth == NTH_LAST:
        return matches[-1]
    return matches[nth - 1] if nth <= len(matches) else None


def _monthly_nth_instants(c: Cadence, baseline: datetime, now: datetime) -> list[datetime]:
    assert c.hour is not None and c.minute is not None and c.dow is not None and c.nth is not None
    out: list[datetime] = []
    cur = _at_local(baseline.replace(day=1), c.hour, c.minute)
    while cur <= now:
        dom = _nth_weekday_of_month(cur.year, cur.month, c.dow, c.nth)
        if dom is not None:
            candidate = cur.replace(day=dom)
            if baseline < candidate <= now:
                out.append(candidate)
        cur = _add_month(cur)
    return out


def _all_instants(cadence: Cadence, baseline: datetime, now: datetime) -> list[datetime]:
    if cadence.kind is CadenceKind.EVERY:
        assert cadence.interval is not None
        return _every_instants(cadence.interval, baseline, now)
    if cadence.kind is CadenceKind.EVERY_ANCHORED:
        return _every_anchored_instants(cadence, baseline, now)
    if cadence.kind is CadenceKind.DAILY:
        return _daily_instants(cadence, baseline, now)
    if cadence.kind is CadenceKind.WEEKLY:
        return _weekly_instants(cadence, baseline, now)
    if cadence.kind is CadenceKind.MONTHLY_NTH:
        return _monthly_nth_instants(cadence, baseline, now)
    return _monthly_instants(cadence, baseline, now)


def owed(
    cadence: Cadence,
    baseline: datetime,
    now: datetime,
    *,
    catchup_window: timedelta,
) -> OwedResult:
    cutoff = now - catchup_window
    instants = _all_instants(cadence, baseline, now)
    in_window = [i for i in instants if i >= cutoff]
    return OwedResult(instants=in_window, expired_count=len(instants) - len(in_window))


def next_due(cadence: Cadence, baseline: datetime, now: datetime) -> datetime:
    """The first scheduled instant strictly after ``max(baseline, now)``."""
    after = max(baseline, now)
    # Reuse the enumerators by projecting one period past the anchor point.
    if cadence.kind is CadenceKind.EVERY:
        assert cadence.interval is not None
        nxt = baseline
        while nxt <= after:
            nxt = nxt + cadence.interval
        return nxt
    if cadence.kind is CadenceKind.EVERY_ANCHORED:
        assert cadence.interval is not None and cadence.anchor is not None
        nxt = datetime(cadence.anchor.year, cadence.anchor.month, cadence.anchor.day,
                       tzinfo=now.tzinfo)
        while nxt <= after:
            nxt = nxt + cadence.interval
        return nxt
    # Wall-clock / nth-weekday forms: enumerate forward and take the first.
    far = after + timedelta(days=400)
    return _all_instants(cadence, after, far)[0]
