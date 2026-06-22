"""Cadence grammar (§7): every:/every:@from=/daily@/weekly:/monthly:<dom>@/
monthly:<dow>#<n>@.

Wall-clock forms (@HH:MM) are interpreted in local time by the due-computation;
this module only parses them into a typed ``Cadence`` value object. Pure;
raises ``CadenceError`` on any malformed input. A ``cron:`` escape hatch is
deliberately not supported (deferred — see §18)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from cc_session_tools.lib.scheduler.duration import DurationError, parse_duration

# Sentinel value stored in Cadence.nth for the ``#last`` form (keeps nth an int).
NTH_LAST = 0


class CadenceError(ValueError):
    """Raised when a cadence string does not match a supported form."""


class CadenceKind(str, Enum):
    EVERY = "every"
    EVERY_ANCHORED = "every_anchored"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    MONTHLY_NTH = "monthly_nth"


_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_EVERY_ANCHORED_RE = re.compile(r"^every:(?P<dur>[^@]+)@from=(?P<date>.+)$")
_DAILY_RE = re.compile(r"^daily@(?P<h>\d{2}):(?P<m>\d{2})$")
_WEEKLY_RE = re.compile(r"^weekly:(?P<dow>[a-z]{3})@(?P<h>\d{2}):(?P<m>\d{2})$")
_MONTHLY_RE = re.compile(r"^monthly:(?P<dom>\d{1,2})@(?P<h>\d{2}):(?P<m>\d{2})$")
_MONTHLY_NTH_RE = re.compile(
    r"^monthly:(?P<dow>[a-z]{3})#(?P<nth>\d|last)@(?P<h>\d{2}):(?P<m>\d{2})$"
)


@dataclass(frozen=True, slots=True)
class Cadence:
    kind: CadenceKind
    interval: timedelta | None = None  # EVERY / EVERY_ANCHORED
    anchor: date | None = None         # EVERY_ANCHORED
    hour: int | None = None            # wall-clock forms
    minute: int | None = None
    dow: int | None = None             # WEEKLY / MONTHLY_NTH (0=Mon..6=Sun)
    dom: int | None = None             # MONTHLY (1..31)
    nth: int | None = None             # MONTHLY_NTH (1..5, or NTH_LAST=0)


def _hm(h: str, m: str, text: str) -> tuple[int, int]:
    hour, minute = int(h), int(m)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise CadenceError(f"invalid time in cadence {text!r}: HH:MM out of range")
    return hour, minute


def _parse_anchor(raw: str, text: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise CadenceError(
            f"invalid @from= date in cadence {text!r}: expected YYYY-MM-DD"
        ) from exc


def parse_cadence(text: str) -> Cadence:
    anchored = _EVERY_ANCHORED_RE.match(text)
    if anchored:
        try:
            interval = parse_duration(anchored.group("dur"))
        except DurationError as exc:
            raise CadenceError(f"invalid anchored cadence {text!r}: {exc}") from exc
        anchor = _parse_anchor(anchored.group("date"), text)
        return Cadence(kind=CadenceKind.EVERY_ANCHORED, interval=interval, anchor=anchor)

    if text.startswith("every:"):
        try:
            interval = parse_duration(text[len("every:"):])
        except DurationError as exc:
            raise CadenceError(f"invalid every: cadence {text!r}: {exc}") from exc
        return Cadence(kind=CadenceKind.EVERY, interval=interval)

    daily = _DAILY_RE.match(text)
    if daily:
        hour, minute = _hm(daily.group("h"), daily.group("m"), text)
        return Cadence(kind=CadenceKind.DAILY, hour=hour, minute=minute)

    weekly = _WEEKLY_RE.match(text)
    if weekly:
        dow_name = weekly.group("dow")
        if dow_name not in _DOW:
            raise CadenceError(f"invalid day-of-week in {text!r}: use mon..sun")
        hour, minute = _hm(weekly.group("h"), weekly.group("m"), text)
        return Cadence(kind=CadenceKind.WEEKLY, dow=_DOW[dow_name], hour=hour, minute=minute)

    nth_match = _MONTHLY_NTH_RE.match(text)
    if nth_match:
        dow_name = nth_match.group("dow")
        if dow_name not in _DOW:
            raise CadenceError(f"invalid day-of-week in {text!r}: use mon..sun")
        nth_raw = nth_match.group("nth")
        if nth_raw == "last":
            nth = NTH_LAST
        else:
            nth = int(nth_raw)
            if not (1 <= nth <= 5):
                raise CadenceError(f"invalid #n in {text!r}: use 1..5 or 'last'")
        hour, minute = _hm(nth_match.group("h"), nth_match.group("m"), text)
        return Cadence(
            kind=CadenceKind.MONTHLY_NTH, dow=_DOW[dow_name], nth=nth, hour=hour, minute=minute
        )

    monthly = _MONTHLY_RE.match(text)
    if monthly:
        dom = int(monthly.group("dom"))
        if not (1 <= dom <= 31):
            raise CadenceError(f"invalid day-of-month in {text!r}: use 1..31")
        hour, minute = _hm(monthly.group("h"), monthly.group("m"), text)
        return Cadence(kind=CadenceKind.MONTHLY, dom=dom, hour=hour, minute=minute)

    raise CadenceError(
        f"unrecognised cadence {text!r}: expected every:<dur> / "
        "every:<dur>@from=YYYY-MM-DD / daily@HH:MM / weekly:<dow>@HH:MM / "
        "monthly:<dom>@HH:MM / monthly:<dow>#<n>@HH:MM"
    )
