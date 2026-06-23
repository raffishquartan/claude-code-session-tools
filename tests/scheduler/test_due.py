# tests/scheduler/test_due.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cc_session_tools.lib.scheduler.cadence import parse_cadence
from cc_session_tools.lib.scheduler.due import OwedResult, next_due, owed

UTC = timezone.utc


def _dt(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_every_no_misses_when_within_interval() -> None:
    c = parse_cadence("every:6h")
    baseline = _dt(2026, 6, 20, 9, 0)
    now = _dt(2026, 6, 20, 12, 0)  # only 3h later
    result = owed(c, baseline, now, catchup_window=timedelta(days=7))
    assert result.instants == []
    assert result.expired_count == 0


def test_every_counts_each_elapsed_interval() -> None:
    c = parse_cadence("every:6h")
    baseline = _dt(2026, 6, 20, 0, 0)
    now = _dt(2026, 6, 20, 19, 0)  # 6h,12h,18h are owed → 3
    result = owed(c, baseline, now, catchup_window=timedelta(days=7))
    assert len(result.instants) == 3


def test_daily_misses_across_three_days() -> None:
    c = parse_cadence("daily@09:00")
    baseline = _dt(2026, 6, 17, 9, 0)         # last success on the 17th at 09:00
    now = _dt(2026, 6, 20, 10, 0)             # 18th, 19th, 20th 09:00 owed → 3
    result = owed(c, baseline, now, catchup_window=timedelta(days=30))
    assert len(result.instants) == 3


def test_catchup_window_drops_expired_instants() -> None:
    c = parse_cadence("daily@09:00")
    baseline = _dt(2026, 6, 1, 9, 0)
    now = _dt(2026, 6, 20, 10, 0)
    # 19 daily instants owed, but only the last 7 days are in-window.
    result = owed(c, baseline, now, catchup_window=timedelta(days=7))
    assert len(result.instants) == 7
    assert result.expired_count == 12


def test_weekly_one_per_week() -> None:
    c = parse_cadence("weekly:mon@09:00")
    baseline = _dt(2026, 6, 1, 9, 0)          # Mon 2026-06-01
    now = _dt(2026, 6, 22, 10, 0)             # Mons 08,15,22 owed → 3
    result = owed(c, baseline, now, catchup_window=timedelta(days=60))
    assert len(result.instants) == 3


def test_monthly_one_per_month() -> None:
    c = parse_cadence("monthly:1@09:00")
    baseline = _dt(2026, 4, 1, 9, 0)
    now = _dt(2026, 6, 2, 10, 0)              # May 1 + Jun 1 owed → 2
    result = owed(c, baseline, now, catchup_window=timedelta(days=120))
    assert len(result.instants) == 2


def test_anchored_interval_is_drift_free() -> None:
    # Fortnightly from 9 Jan 2026; instants land on the fixed 14-day grid
    # regardless of baseline, never drifting.
    c = parse_cadence("every:2w@from=2026-01-09")
    baseline = _dt(2026, 1, 9, 0, 0)
    now = _dt(2026, 2, 7, 0, 0)               # 23 Jan + 6 Feb owed → 2
    result = owed(c, baseline, now, catchup_window=timedelta(days=90))
    assert result.instants == [_dt(2026, 1, 23), _dt(2026, 2, 6)]


def test_anchored_interval_baseline_after_anchor_stays_on_grid() -> None:
    # Even if the last success was off-grid, the next instants are on the
    # original anchor grid (drift-free), not baseline + interval.
    c = parse_cadence("every:2w@from=2026-01-09")
    baseline = _dt(2026, 1, 20, 12, 0)        # mid-cycle, off the grid
    now = _dt(2026, 2, 7, 0, 0)               # 23 Jan + 6 Feb owed → 2
    result = owed(c, baseline, now, catchup_window=timedelta(days=90))
    assert result.instants == [_dt(2026, 1, 23), _dt(2026, 2, 6)]


def test_monthly_nth_weekday_third_thursday() -> None:
    c = parse_cadence("monthly:thu#3@09:00")
    baseline = _dt(2026, 5, 1, 9, 0)
    now = _dt(2026, 6, 30, 10, 0)             # 3rd Thu May (21st) + Jun (18th) → 2
    result = owed(c, baseline, now, catchup_window=timedelta(days=120))
    assert result.instants == [_dt(2026, 5, 21, 9, 0), _dt(2026, 6, 18, 9, 0)]


def test_monthly_nth_weekday_last_friday() -> None:
    c = parse_cadence("monthly:fri#last@18:00")
    baseline = _dt(2026, 1, 1, 18, 0)
    now = _dt(2026, 2, 28, 19, 0)             # last Fri Jan (30th) + Feb (27th) → 2
    result = owed(c, baseline, now, catchup_window=timedelta(days=120))
    assert result.instants == [_dt(2026, 1, 30, 18, 0), _dt(2026, 2, 27, 18, 0)]


def test_monthly_nth_fifth_weekday_skips_months_without_five() -> None:
    # #5 Thursday: only months with five Thursdays produce an instant; others skip.
    c = parse_cadence("monthly:thu#5@09:00")
    baseline = _dt(2026, 1, 1, 9, 0)
    # Jan has five Thursdays (29th); Feb and Mar do not.
    # Apr also has five Thursdays (30th), so stop before it to keep the test focused.
    now = _dt(2026, 4, 1, 10, 0)
    result = owed(c, baseline, now, catchup_window=timedelta(days=200))
    # Jan 2026 has five Thursdays (29th); Feb/Mar 2026 do not → exactly one.
    assert result.instants == [_dt(2026, 1, 29, 9, 0)]


def test_monthly_dom_clamps_to_last_day() -> None:
    # dom=31 clamps to the last day of months with fewer days.
    c = parse_cadence("monthly:31@09:00")
    baseline = _dt(2026, 1, 31, 9, 0)
    now = _dt(2026, 3, 31, 10, 0)
    result = owed(c, baseline, now, catchup_window=timedelta(days=120))
    # Feb clamps to the 28th (2026 is not a leap year); Mar is the 31st → 2.
    assert result.instants == [_dt(2026, 2, 28, 9, 0), _dt(2026, 3, 31, 9, 0)]


def test_next_due_after_baseline() -> None:
    c = parse_cadence("daily@09:00")
    baseline = _dt(2026, 6, 20, 9, 0)
    now = _dt(2026, 6, 20, 12, 0)
    assert next_due(c, baseline, now) == _dt(2026, 6, 21, 9, 0)


def test_next_due_anchored_every() -> None:
    # Grid: 2026-01-09, 2026-01-23, 2026-02-06, ...
    c = parse_cadence("every:2w@from=2026-01-09")
    baseline = _dt(2026, 1, 9, 0, 0)
    now = _dt(2026, 1, 21, 12, 0)  # between Jan 9 and Jan 23
    assert next_due(c, baseline, now) == _dt(2026, 1, 23, 0, 0)


def test_next_due_weekly() -> None:
    c = parse_cadence("weekly:mon@09:00")
    baseline = _dt(2026, 6, 15, 9, 0)  # Mon 2026-06-15
    now = _dt(2026, 6, 17, 12, 0)      # Wednesday
    assert next_due(c, baseline, now) == _dt(2026, 6, 22, 9, 0)  # next Monday


def test_next_due_monthly_dom() -> None:
    c = parse_cadence("monthly:1@09:00")
    baseline = _dt(2026, 6, 1, 9, 0)
    now = _dt(2026, 6, 10, 12, 0)
    assert next_due(c, baseline, now) == _dt(2026, 7, 1, 9, 0)


def test_next_due_monthly_nth_weekday() -> None:
    # 3rd Thursday: May 2026 was the 21st; Jun 2026 is the 18th.
    c = parse_cadence("monthly:thu#3@09:00")
    baseline = _dt(2026, 5, 21, 9, 0)
    now = _dt(2026, 5, 22, 10, 0)  # just after May 3rd Thu
    assert next_due(c, baseline, now) == _dt(2026, 6, 18, 9, 0)


def test_next_due_monthly_nth_last_weekday() -> None:
    # Last Friday: Jan 2026 was 30th; Feb 2026 was 27th.
    c = parse_cadence("monthly:fri#last@18:00")
    baseline = _dt(2026, 1, 30, 18, 0)
    now = _dt(2026, 1, 31, 10, 0)  # just after last-Friday Jan
    assert next_due(c, baseline, now) == _dt(2026, 2, 27, 18, 0)
