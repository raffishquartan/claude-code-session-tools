from __future__ import annotations

from datetime import datetime, timezone

from cc_session_tools.lib.timefmt import HUMAN_UTC_FORMAT, format_utc, format_utc_epoch


def test_format_utc_renders_text_form() -> None:
    assert format_utc(datetime(2026, 10, 5, 14, 30, 59, tzinfo=timezone.utc)) == "2026-10-05 14:30 UTC"


def test_format_utc_epoch_renders_text_form() -> None:
    assert format_utc_epoch(1_790_000_000) == "2026-09-21 14:13 UTC"


def test_format_utc_epoch_defaults_to_now() -> None:
    import re

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", format_utc_epoch())


def test_format_constant_is_the_documented_form() -> None:
    assert HUMAN_UTC_FORMAT == "%Y-%m-%d %H:%M UTC"
