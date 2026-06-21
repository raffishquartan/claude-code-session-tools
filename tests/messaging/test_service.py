# tests/messaging/test_service.py  (send portion)
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import service, store
from cc_session_tools.lib.messaging.message import parse


def _sender() -> service.SendRequest:
    return service.SendRequest(
        from_project="oneshot",
        from_session="20260615-oneshot-x",
        from_uuid="sender-uuid",
        to_kind="project",
        to_value="alpha",
        to_partition="projects/alpha",
        subject="Hello there",
        body="Body text.",
        attachments=["/abs/a.md"],
        thread=None,
    )


def test_send_writes_message_to_partition_inbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(_sender())
    files = list((tmp_path / "projects" / "alpha" / "inbox").glob("*.md"))
    assert len(files) == 1
    m = parse(files[0].read_text())
    assert m.id == mid
    assert m.status == "sent"
    assert m.subject == "Hello there"
    assert m.attachments == ["/abs/a.md"]
