from __future__ import annotations

from datetime import date, timedelta

import pytest

from cc_session_tools.lib.scheduler.cadence import (
    Cadence,
    CadenceError,
    CadenceKind,
    parse_cadence,
)

# Sentinel for "last" in the nth-weekday form (so `nth` stays an int field).
LAST = 0


def test_every_parses_interval() -> None:
    c = parse_cadence("every:6h")
    assert c.kind is CadenceKind.EVERY
    assert c.interval == timedelta(hours=6)


def test_every_anchored_parses_interval_and_date() -> None:
    c = parse_cadence("every:2w@from=2026-01-09")
    assert c.kind is CadenceKind.EVERY_ANCHORED
    assert c.interval == timedelta(weeks=2)
    assert c.anchor == date(2026, 1, 9)


def test_every_anchored_accepts_sub_day_unit() -> None:
    c = parse_cadence("every:6h@from=2026-01-09")
    assert c.kind is CadenceKind.EVERY_ANCHORED
    assert c.interval == timedelta(hours=6)
    assert c.anchor == date(2026, 1, 9)


def test_daily_parses_wall_clock() -> None:
    c = parse_cadence("daily@09:00")
    assert c.kind is CadenceKind.DAILY
    assert (c.hour, c.minute) == (9, 0)


def test_weekly_parses_dow_and_time() -> None:
    c = parse_cadence("weekly:mon@07:30")
    assert c.kind is CadenceKind.WEEKLY
    assert c.dow == 0  # Monday
    assert (c.hour, c.minute) == (7, 30)


def test_weekly_accepts_sunday() -> None:
    assert parse_cadence("weekly:sun@23:59").dow == 6


def test_monthly_parses_dom_and_time() -> None:
    c = parse_cadence("monthly:1@00:00")
    assert c.kind is CadenceKind.MONTHLY
    assert c.dom == 1
    assert (c.hour, c.minute) == (0, 0)


def test_monthly_nth_weekday_parses() -> None:
    c = parse_cadence("monthly:thu#3@09:00")
    assert c.kind is CadenceKind.MONTHLY_NTH
    assert c.dow == 3  # Thursday
    assert c.nth == 3
    assert (c.hour, c.minute) == (9, 0)


def test_monthly_nth_weekday_last() -> None:
    c = parse_cadence("monthly:fri#last@18:00")
    assert c.kind is CadenceKind.MONTHLY_NTH
    assert c.dow == 4  # Friday
    assert c.nth == LAST  # 0 sentinel for "last"
    assert (c.hour, c.minute) == (18, 0)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "every:",
        "every:0h",
        "every:2w@from=",            # missing date
        "every:2w@from=2026-13-09",  # bad month
        "every:2w@from=not-a-date",  # malformed date
        "every:@from=2026-01-09",    # missing duration
        "daily@9",                   # missing minutes
        "daily@24:00",               # hour out of range
        "daily@09:60",               # minute out of range
        "weekly:funday@09:00",
        "weekly:mon",                # missing time
        "monthly:0@09:00",           # dom < 1
        "monthly:32@09:00",          # dom > 31
        "monthly:thu#0@09:00",       # nth < 1 (and not 'last')
        "monthly:thu#6@09:00",       # nth > 5
        "monthly:funday#3@09:00",    # bad dow
        "monthly:thu#3",             # missing time
        'cron:"0 9 * * *"',          # deferred escape hatch
        "hourly@09:00",
    ],
)
def test_invalid_raises(text: str) -> None:
    with pytest.raises(CadenceError):
        parse_cadence(text)


def test_cadence_is_frozen() -> None:
    c = parse_cadence("daily@09:00")
    with pytest.raises(AttributeError):
        c.hour = 10  # type: ignore[misc]
