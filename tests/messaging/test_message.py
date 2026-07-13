# tests/messaging/test_message.py
from __future__ import annotations

import pytest

from cc_session_tools.lib.messaging.message import (
    Message,
    parse,
    serialise,
)


def _sample() -> Message:
    return Message(
        id="20260620T231500Z-a1b2",
        schema=1,
        from_project="oneshot",
        from_session="20260615-oneshot-inter-session-message-skill",
        from_uuid="8dbed047-0000-0000-0000-000000000000",
        to_kind="session",
        to_value="aaaa1111-2222-3333-4444-555566667777",
        to_location="projects/oneshot",
        subject="Short human subject",
        sent_at="2026-06-20T23:15:00Z",
        status="sent",
        read_at=None,
        read_by_uuid=None,
        read_by_session=None,
        claimed_at=None,
        receipt_shown=False,
        thread=None,
        attachments=["/abs/path/to/file.md"],
        body="Free-form markdown body.\nSecond line.\n",
    )


def test_round_trip_preserves_all_fields() -> None:
    m = _sample()
    assert parse(serialise(m)) == m


def test_serialise_emits_frontmatter_then_body() -> None:
    text = serialise(_sample())
    assert text.startswith("---\n")
    assert "\n---\n" in text
    assert text.rstrip().endswith("Second line.")


def test_parse_rejects_missing_frontmatter() -> None:
    with pytest.raises(ValueError):
        parse("no frontmatter here\n")


def test_parse_rejects_unterminated_frontmatter() -> None:
    with pytest.raises(ValueError, match="not terminated"):
        parse("---\nid: x\nstatus: sent\n")


def test_parse_rejects_missing_required_field() -> None:
    with pytest.raises(ValueError, match="missing required"):
        parse("---\nto_kind: session\nstatus: sent\n---\n\nbody\n")


def test_parse_rejects_invalid_to_kind() -> None:
    with pytest.raises(ValueError, match="invalid to_kind"):
        parse("---\nto_kind: bogus\nstatus: sent\n---\n\nbody\n")


def test_parse_rejects_invalid_status() -> None:
    with pytest.raises(ValueError, match="invalid status"):
        parse("---\nto_kind: session\nstatus: bogus\n---\n\nbody\n")
