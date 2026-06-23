from __future__ import annotations

from datetime import timedelta

import pytest

from cc_session_tools.lib.scheduler.duration import DurationError, parse_duration


@pytest.mark.parametrize(
    "text,expected",
    [
        ("30s", timedelta(seconds=30)),
        ("5m", timedelta(minutes=5)),
        ("6h", timedelta(hours=6)),
        ("7d", timedelta(days=7)),
        ("1d", timedelta(days=1)),
        ("2w", timedelta(weeks=2)),
        ("1w", timedelta(weeks=1)),
    ],
)
def test_parse_valid(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "0s", "-5m", "5", "h", "5x", "5.5h", "5 h", "abc", "0w"])
def test_parse_invalid_raises(text: str) -> None:
    with pytest.raises(DurationError):
        parse_duration(text)
