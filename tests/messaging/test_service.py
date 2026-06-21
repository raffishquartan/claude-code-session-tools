# tests/messaging/test_service.py  (send portion)
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import service, store
from cc_session_tools.lib.messaging.addressing import SessionContext
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


def test_read_one_returns_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(_sender())
    msg = service.read_one(mid)
    assert msg is not None
    assert msg.subject == "Hello there"


def test_read_one_missing_id_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    assert service.read_one("nope") is None


def test_list_messages_filters_by_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    service.send(_sender())
    rows = service.list_messages(status="sent")
    assert len(rows) == 1
    assert service.list_messages(status="read") == []


def _sender_to(partition: str, value: str, from_uuid: str) -> service.SendRequest:
    return service.SendRequest(
        from_project="oneshot", from_session="s", from_uuid=from_uuid,
        to_kind="project", to_value=value, to_partition=partition,
        subject="Subj", body="B", attachments=[], thread=None,
    )


def test_list_messages_filters_by_partition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    service.send(_sender_to("projects/alpha", "alpha", "u1"))
    service.send(_sender_to("projects/beta", "beta", "u1"))
    assert len(service.list_messages(partition="projects/alpha")) == 1
    assert len(service.list_messages(partition="projects/beta")) == 1
    assert len(service.list_messages()) == 2


def test_list_messages_filters_by_from_uuid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    service.send(_sender_to("projects/alpha", "alpha", "sender-1"))
    service.send(_sender_to("projects/alpha", "alpha", "sender-2"))
    rows = service.list_messages(from_uuid="sender-1")
    assert len(rows) == 1


def test_list_messages_skips_malformed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    service.send(_sender())
    # A stale/hand-edited file in the store must not abort the listing.
    bad = store.ensure_inbox_dir("projects/alpha") / "20990101T000000Z-bbbb__broken.md"
    bad.write_text("not a valid message\n", encoding="utf-8")
    rows = service.list_messages()
    assert len(rows) == 1


def _ctx(
    uuid: str = "me-uuid",
    project: str = "alpha",
    partition: str = "projects/alpha",
) -> SessionContext:
    return SessionContext(uuid=uuid, project=project, partition=partition)


def _send_to_session(target_uuid: str) -> str:
    return service.send(service.SendRequest(
        from_project="oneshot", from_session="s", from_uuid="sender",
        to_kind="session", to_value=target_uuid, to_partition="projects/alpha",
        subject="For you", body="Body.", attachments=[], thread=None,
    ))


def test_deliver_auto_reads_session_message_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_to_session("me-uuid")
    digest1 = service.deliver(_ctx(), mode="full")
    assert mid in digest1
    read = service.read_one(mid)
    assert read is not None
    assert read.status == "read"
    # Second sweep: cursor has advanced; the message is not re-surfaced.
    digest2 = service.deliver(_ctx(), mode="incremental")
    assert mid not in digest2


def test_deliver_does_not_auto_read_other_sessions_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_to_session("someone-else")
    digest = service.deliver(_ctx(), mode="full")
    assert mid not in digest
    read = service.read_one(mid)
    assert read is not None
    assert read.status == "sent"


def test_deliver_surfaces_receipt_once_to_sender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_to_session("me-uuid")
    # Recipient reads it.
    service.deliver(_ctx(uuid="me-uuid"), mode="full")
    # Sender sweeps and sees a one-time receipt.
    sender_ctx = _ctx(uuid="sender", project="alpha", partition="projects/alpha")
    d1 = service.deliver(sender_ctx, mode="full")
    assert "read" in d1.lower() and mid in d1
    d2 = service.deliver(sender_ctx, mode="full")
    assert mid not in d2  # receipt_shown flipped


def test_deliver_surfaces_description_as_proposal_without_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="description", to_value="whoever works on X",
        to_partition="_global", subject="task", body="b", attachments=[], thread=None,
    ))
    digest = service.deliver(_ctx(), mode="full")
    assert mid in digest
    read = service.read_one(mid)
    assert read is not None
    assert read.status == "sent"  # candidate, not read


def test_deliver_skips_malformed_file_in_swept_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_to_session("me-uuid")
    # A stale/hand-edited file in a swept partition must not abort the sweep.
    bad = store.ensure_inbox_dir("projects/alpha") / "20990101T000000Z-bbbb__broken.md"
    bad.write_text("not a valid message\n", encoding="utf-8")
    digest = service.deliver(_ctx(), mode="full")
    assert mid in digest
