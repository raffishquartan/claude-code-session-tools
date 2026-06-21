# tests/messaging/test_cursor.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import cursor as cur
from cc_session_tools.lib.messaging.message import Message


def _msg(mid: str, location: str) -> Message:
    return Message(
        id=mid, schema=1, from_project="x", from_session="x", from_uuid="s",
        to_kind="project", to_value="alpha", to_location=location, subject="s",
        sent_at="2026-06-20T00:00:00Z", status="sent", read_at=None,
        read_by_uuid=None, read_by_session=None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=[], body="b",
    )


def test_empty_cursor_treats_everything_as_new() -> None:
    c = cur.Cursor.empty()
    assert cur.is_new(_msg("20260620T000000Z-0001", "projects/alpha"), c)


def test_advance_then_older_is_not_new() -> None:
    c = cur.Cursor.empty()
    newer = _msg("20260620T120000Z-0002", "projects/alpha")
    older = _msg("20260620T110000Z-0001", "projects/alpha")
    c = cur.advance(c, newer)
    assert not cur.is_new(newer, c)
    assert not cur.is_new(older, c)


def test_advance_is_per_partition() -> None:
    c = cur.Cursor.empty()
    c = cur.advance(c, _msg("20260620T120000Z-0002", "projects/alpha"))
    # A message in a different partition is unaffected by alpha's high-water.
    assert cur.is_new(_msg("20260620T010000Z-0001", "repos/beta"), c)


def test_save_and_load_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    c = cur.advance(cur.Cursor.empty(), _msg("20260620T120000Z-0002", "projects/alpha"))
    cur.save("uuid-123", c)
    assert cur.load("uuid-123") == c


def test_load_missing_cursor_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    assert cur.load("never-seen") == cur.Cursor.empty()


def test_advance_same_message_twice_is_idempotent() -> None:
    msg = _msg("20260620T120000Z-0002", "projects/alpha")
    once = cur.advance(cur.Cursor.empty(), msg)
    twice = cur.advance(once, msg)
    assert once == twice
    assert not cur.is_new(msg, twice)
