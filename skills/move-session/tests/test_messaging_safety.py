# skills/move-session/tests/test_messaging_safety.py
from __future__ import annotations

from pathlib import Path

import pytest

# The messaging-safety helper lives in the messaging lib so move_session can
# import it; this test exercises it directly.
from cc_session_tools.lib.messaging.move_safety import (
    refresh_display_tags,
    relocate_cursor,
)
from cc_session_tools.lib.messaging import service, store, cursor


def test_refresh_display_tags_updates_pending_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(service.SendRequest(
        from_project="oneshot", from_session="old-tag", from_uuid="moved-uuid",
        to_kind="project", to_value="alpha", to_partition="projects/alpha",
        subject="s", body="b", attachments=[], thread=None,
    ))
    refresh_display_tags(uuid="moved-uuid", new_tag="new-tag")
    updated = service.read_one(mid)
    assert updated is not None
    assert updated.from_session == "new-tag"


def test_refresh_display_tags_skips_malformed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(service.SendRequest(
        from_project="oneshot", from_session="old-tag", from_uuid="moved-uuid",
        to_kind="project", to_value="alpha", to_partition="projects/alpha",
        subject="s", body="b", attachments=[], thread=None,
    ))
    bad = store.ensure_inbox_dir("projects/alpha") / "20990101T000000Z-bbbb__broken.md"
    bad.write_text("not a valid message\n", encoding="utf-8")
    # One corrupt file must not abort the rename; the good message still updates.
    assert refresh_display_tags(uuid="moved-uuid", new_tag="new-tag") == 1
    updated = service.read_one(mid)
    assert updated is not None
    assert updated.from_session == "new-tag"


def test_relocate_cursor_preserves_cursor_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    c = cursor.advance(cursor.Cursor.empty(), service.read_one(
        service.send(service.SendRequest(
            from_project="o", from_session="t", from_uuid="u",
            to_kind="project", to_value="alpha", to_partition="projects/alpha",
            subject="s", body="b", attachments=[], thread=None,
        ))
    ))
    cursor.save("moved-uuid", c)
    relocate_cursor(uuid="moved-uuid", old_partition="projects/old", new_partition="projects/new")
    loaded = cursor.load("moved-uuid")
    # The cursor still exists and is keyed on the same uuid (uuid-keyed survives moves).
    assert loaded == c
