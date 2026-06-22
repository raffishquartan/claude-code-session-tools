"""Parse the ``<int><unit>`` duration grammar (units s/m/h/d/w) into a
``timedelta``. Pure; raises ``DurationError`` on any malformed input."""
from __future__ import annotations

import re
from datetime import timedelta

_DURATION_RE = re.compile(r"^(?P<value>\d+)(?P<unit>[smhdw])$")
_UNIT_KW = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


class DurationError(ValueError):
    """Raised when a duration string does not match ``<positive-int><s|m|h|d>``."""


def parse_duration(text: str) -> timedelta:
    match = _DURATION_RE.match(text)
    if match is None:
        raise DurationError(
            f"invalid duration {text!r}: expected <positive-integer><s|m|h|d|w>, e.g. '6h'"
        )
    value = int(match.group("value"))
    if value <= 0:
        raise DurationError(f"invalid duration {text!r}: value must be positive")
    return timedelta(**{_UNIT_KW[match.group("unit")]: value})
