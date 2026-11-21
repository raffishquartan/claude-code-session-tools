# tests/messaging/test_service.py  (send portion)
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import service
from cc_session_tools.lib.messaging.addressing import SessionContext


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
    m = service.read_one(mid)
    assert m is not None
    assert m.to_location == "projects/alpha"
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


def test_list_messages_returns_compact_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    service.send(_sender())
    rows = service.list_messages()
    assert len(rows) == 1
    assert rows[0].to_kind == "project" and rows[0].to_value == "alpha"


def test_list_messages_rows_include_sent_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    service.send(_sender())
    rows = service.list_messages()
    assert len(rows) == 1
    assert rows[0].sent_at == service.read_one(rows[0].id).sent_at


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


def test_deliver_project_message_auto_read_by_first_session_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="project", to_value="alpha", to_partition="projects/alpha",
        subject="team ping", body="b", attachments=[], thread=None,
    ))
    a = _ctx(uuid="sess-A", project="alpha", partition="projects/alpha")
    b = _ctx(uuid="sess-B", project="alpha", partition="projects/alpha")
    d_a = service.deliver(a, mode="full")
    d_b = service.deliver(b, mode="full")
    assert mid in d_a and "not yet handled" not in d_a   # first reader wins the auto-read line
    assert mid in d_b.split("not yet handled", 1)[1]     # later session sees it as unhandled
    read = service.read_one(mid)
    assert read is not None and read.read_by_uuid == "sess-A"


def _insert_sent(mid: str, to_kind: str, to_value: str, location: str) -> None:
    from cc_session_tools.lib.messaging import repository
    from cc_session_tools.lib.messaging.message import Message

    repository.insert(Message(
        id=mid, schema=1, from_project="o", from_session="s", from_uuid="sender",
        to_kind=to_kind, to_value=to_value, to_location=location,  # type: ignore[arg-type]
        subject="s", sent_at="2026-01-01T00:00:00Z", status="sent", read_at=None,
        read_by_uuid=None, read_by_session=None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=[], body="b",
    ))


@pytest.mark.parametrize(
    ("to_kind", "to_value", "location"),
    [("session", "me-uuid", "_global"), ("project", "alpha", "projects/alpha")],
)
def test_unarchived_never_read_message_reaches_a_recipient_whose_cursor_passed_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    to_kind: str, to_value: str, location: str,
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    old, newer = "20260101T000000Z-0001", "20260101T000000Z-0002"
    _insert_sent(old, to_kind, to_value, location)
    service.archive(old, datetime.now(timezone.utc))  # archived without ever being read
    _insert_sent(newer, to_kind, to_value, location)
    ctx = _ctx(uuid="me-uuid", project="alpha", partition="projects/alpha")
    service.deliver(ctx, mode="full")  # delivers `newer`; the cursor is now past `old`
    assert service.unarchive(old).status == "sent"
    digest = service.deliver(ctx, mode="full")
    assert old in digest
    restored = service.read_one(old)
    assert restored is not None and restored.status == "read"
    assert restored.read_by_uuid == "me-uuid"


def test_unhandled_hint_is_a_runnable_claim_command_and_claim_clears_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="project", to_value="alpha", to_partition="projects/alpha",
        subject="team ping", body="b", attachments=[], thread=None,
    ))
    a = _ctx(uuid="sess-A", project="alpha", partition="projects/alpha")
    b = _ctx(uuid="sess-B", project="alpha", partition="projects/alpha")
    service.deliver(a, mode="full")
    d_b = service.deliver(b, mode="full")
    section = d_b.split("not yet handled", 1)[1]
    assert mid in section
    assert "ccmsg claim <id> --uuid sess-B --session alpha" in d_b
    assert "run `ccmsg archive" not in d_b

    service.claim(mid, service.Claimer(uuid="sess-A", session="alpha"))

    d_b2 = service.deliver(_ctx(uuid="sess-C", project="alpha", partition="projects/alpha"),
                           mode="full")
    assert mid not in d_b2


def test_claim_hint_quotes_values_and_drops_flags_without_a_uuid() -> None:
    quoted = service._claim_hint(_ctx(uuid="sess-A", project="my project"))
    assert quoted == "ccmsg claim <id> --uuid sess-A --session 'my project'"
    assert service._claim_hint(_ctx(uuid="", project="alpha")) == "ccmsg claim <id>"


def _send_to_session_in_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, target_uuid: str
) -> str:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    return service.send(service.SendRequest(
        from_project="oneshot", from_session="s", from_uuid="sender",
        to_kind="session", to_value=target_uuid, to_partition="projects/alpha",
        subject="For you", body="Body.", attachments=[], thread=None,
    ))


def test_claim_flips_status_and_blocks_second_claimer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    from cc_session_tools.lib.messaging.lock import AlreadyClaimedError
    mid = service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="description", to_value="X", to_partition="_global",
        subject="task", body="b", attachments=[], thread=None,
    ))
    claimer = service.Claimer(uuid="me", session="alpha")
    msg = service.claim(mid, claimer)
    assert msg.status == "claimed"
    assert msg.read_by_uuid == "me"
    with pytest.raises(AlreadyClaimedError):
        service.claim(mid, service.Claimer(uuid="other", session="beta"))


def test_archive_moves_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timezone
    mid = _send_to_session_in_tmp(monkeypatch, tmp_path, "me-uuid")
    service.archive(mid, datetime(2026, 6, 20, tzinfo=timezone.utc))
    result = service.read_one(mid)
    assert result is not None
    assert result.status == "archived"


def test_archive_blocked_while_claim_lock_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timezone

    from cc_session_tools.lib.messaging.lock import AlreadyClaimedError, claim_lock

    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="description", to_value="X", to_partition="_global",
        subject="task", body="b", attachments=[], thread=None,
    ))
    # A manual archive must not race a claim in flight for the same id.
    with claim_lock(mid):
        with pytest.raises(AlreadyClaimedError):
            service.archive(mid, datetime(2026, 6, 20, tzinfo=timezone.utc))


def test_format_sent_at_renders_utc_text_form() -> None:
    from cc_session_tools.lib.messaging.service import format_sent_at

    assert format_sent_at("2026-10-05T14:30:59Z") == "2026-10-05 14:30 UTC"


@pytest.mark.parametrize("stored", ["2026-10-05T14:30:00.123+00:00", "not a time", ""])
def test_format_sent_at_returns_an_unparseable_value_as_stored(stored: str) -> None:
    from cc_session_tools.lib.messaging.service import format_sent_at

    assert format_sent_at(stored) == stored


def _send_description(subject: str) -> str:
    return service.send(service.SendRequest(
        from_project="o", from_session="s", from_uuid="sender",
        to_kind="description", to_value="whoever works on X",
        to_partition="_global", subject=subject, body="b", attachments=[], thread=None,
    ))


def test_full_deliver_relists_previously_shown_unclaimed_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_description("task")
    assert mid in service.deliver(_ctx(), mode="full")
    # Cursor has advanced, but a later full delivery (new session start) lists it again.
    assert mid in service.deliver(_ctx(), mode="full")
    # An incremental delivery does not repeat it.
    assert mid not in service.deliver(_ctx(), mode="incremental")


def test_full_deliver_lists_new_proposal_once_with_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_description("task")
    digest = service.deliver(_ctx(), mode="full")  # new and pending: must not duplicate
    assert digest.count(mid) == 1
    line = digest.split(mid, 1)[1].splitlines()[0]
    assert "just now" in line or "ago" in line


def test_full_deliver_does_not_relist_claimed_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_description("task")
    service.deliver(_ctx(), mode="full")
    service.claim(mid, service.Claimer(uuid="claimer", session="s"))
    assert mid not in service.deliver(_ctx(), mode="full")


def test_full_deliver_caps_pending_list_and_reports_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    ids = [_send_description(f"task {i}") for i in range(service.PENDING_LIST_CAP + 2)]
    service.deliver(_ctx(), mode="full")  # first sight; cursor advances
    digest = service.deliver(_ctx(), mode="full")
    listed = [i for i in sorted(ids) if i in digest]
    assert len(listed) == service.PENDING_LIST_CAP
    # Oldest (lowest id) first; the rest are omitted.
    assert listed == sorted(ids)[: service.PENDING_LIST_CAP]
    assert "+2 more" in digest and "ccmsg list --status sent" in digest


def test_full_deliver_cap_keeps_oldest_pending_when_new_proposals_arrive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cc_session_tools.lib.messaging import repository
    from cc_session_tools.lib.messaging.message import Message

    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    old = [f"20260101T000000Z-000{i}" for i in range(3)]
    for mid in old:
        repository.insert(Message(
            id=mid, schema=1, from_project="o", from_session="s", from_uuid="sender",
            to_kind="description", to_value="whoever works on X", to_location="_global",
            subject="old", sent_at="2026-01-01T00:00:00Z", status="sent", read_at=None,
            read_by_uuid=None, read_by_session=None, claimed_at=None,
            receipt_shown=False, thread=None, attachments=[], body="b",
        ))
    service.deliver(_ctx(), mode="full")  # old ones seen once; cursor passes them
    new = [_send_description(f"new {i}") for i in range(4)]  # ids newer than the old ones
    digest = service.deliver(_ctx(), mode="full")  # new ones are swept, old ones re-listed
    # The cap keeps the longest-waiting five: all three old ones and two new ones.
    assert all(i in digest for i in old)
    assert sum(i in digest for i in new) == service.PENDING_LIST_CAP - len(old)
    assert "+2 more" in digest


def test_full_deliver_lists_read_message_until_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_to_session("me-uuid")
    first = service.deliver(_ctx(), mode="full")
    assert first.count(mid) == 1  # auto-read this run: shown once, not duplicated
    later = service.deliver(_ctx(), mode="full")  # next session start
    assert mid in later and "not yet handled" in later
    assert service.deliver(_ctx(), mode="incremental") == ""
    service.archive(mid, datetime.now(timezone.utc))
    assert mid not in service.deliver(_ctx(), mode="full")


def test_full_deliver_lists_project_message_read_by_another_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = service.send(_sender())  # project=alpha
    service.deliver(_ctx(uuid="first-session"), mode="full")  # auto-reads it
    digest = service.deliver(_ctx(uuid="second-session"), mode="full")
    assert mid in digest and "not yet handled" in digest


def test_full_deliver_does_not_list_other_sessions_read_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_to_session("someone-else")
    service.deliver(_ctx(uuid="someone-else"), mode="full")  # recipient reads it
    assert mid not in service.deliver(_ctx(uuid="me-uuid"), mode="full")


def test_full_deliver_caps_unhandled_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    for _ in range(service.PENDING_LIST_CAP + 3):
        service.send(_sender())
    service.deliver(_ctx(), mode="full")  # auto-reads all
    digest = service.deliver(_ctx(), mode="full")
    assert "+3 more" in digest
