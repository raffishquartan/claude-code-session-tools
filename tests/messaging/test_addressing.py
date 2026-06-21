# tests/messaging/test_addressing.py
from __future__ import annotations

from cc_session_tools.lib.messaging.addressing import (
    MatchKind,
    SessionContext,
    targets,
)
from cc_session_tools.lib.messaging.message import Message


def _msg(**over: object) -> Message:
    base = dict(
        id="20260620T000000Z-0001", schema=1, from_project="x",
        from_session="x", from_uuid="sender", to_kind="session",
        to_value="me-uuid", to_location="projects/alpha",
        subject="s", sent_at="2026-06-20T00:00:00Z", status="sent",
        read_at=None, read_by_uuid=None, read_by_session=None,
        claimed_at=None, receipt_shown=False, thread=None,
        attachments=[], body="b",
    )
    base.update(over)
    return Message(**base)  # type: ignore[arg-type]


def _ctx() -> SessionContext:
    return SessionContext(uuid="me-uuid", project="alpha", partition="projects/alpha")


def test_session_addressed_to_my_uuid_matches() -> None:
    assert targets(_msg(to_kind="session", to_value="me-uuid"), _ctx()) is MatchKind.RECIPIENT


def test_session_addressed_to_other_uuid_no_match() -> None:
    assert targets(_msg(to_kind="session", to_value="other"), _ctx()) is MatchKind.NONE


def test_project_addressed_to_my_project_matches() -> None:
    assert targets(_msg(to_kind="project", to_value="alpha"), _ctx()) is MatchKind.RECIPIENT


def test_project_addressed_to_other_project_no_match() -> None:
    assert targets(_msg(to_kind="project", to_value="beta"), _ctx()) is MatchKind.NONE


def test_description_addressed_is_a_candidate() -> None:
    assert targets(_msg(to_kind="description", to_value="whoever does X"), _ctx()) is MatchKind.CANDIDATE


def test_already_read_message_does_not_match_recipient_again() -> None:
    m = _msg(to_kind="session", to_value="me-uuid", status="read")
    assert targets(m, _ctx()) is MatchKind.NONE
