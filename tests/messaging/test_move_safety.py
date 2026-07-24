# tests/messaging/test_move_safety.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import move_safety, repository
from cc_session_tools.lib.messaging.message import Message


def _msg(mid: str, **over) -> Message:
    base = dict(
        id=mid, schema=1, from_project="p", from_session="old", from_uuid="u",
        to_kind="project", to_value="alpha", to_location="projects/alpha",
        subject="s", sent_at="2026-06-20T00:00:00Z", status="sent", read_at=None,
        read_by_uuid=None, read_by_session=None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=[], body="b",
    )
    base.update(over)
    return Message(**base)


def test_refresh_display_tags_updates_pending_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    repository.insert(_msg("20260620T000000Z-0001", from_uuid="u", from_session="old"))
    repository.insert(_msg("20260620T000000Z-0002", from_uuid="u", from_session="old",
                           status="archived"))
    assert move_safety.refresh_display_tags(uuid="u", new_tag="new") == 1
    assert repository.get_by_id("20260620T000000Z-0001").from_session == "new"
    assert repository.get_by_id("20260620T000000Z-0002").from_session == "old"


def test_relocate_cursor_is_noop_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    move_safety.relocate_cursor(uuid="u", old_partition="a", new_partition="b")  # must not raise
