# src/cc_session_tools/lib/messaging/service.py
"""Shared messaging service used by both the ccmsg CLI and the delivery hook.

This module holds business logic; argparse validation stays in the CLI."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from cc_session_tools.lib.messaging.message import Message, ToKind, write_atomic
from cc_session_tools.lib.messaging.store import (
    ensure_inbox_dir,
    generate_id,
    message_filename,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class SendRequest:
    from_project: str
    from_session: str
    from_uuid: str
    to_kind: ToKind
    to_value: str
    to_partition: str
    subject: str
    body: str
    attachments: list[str] = field(default_factory=list)
    thread: str | None = None


def send(request: SendRequest) -> str:
    """Build and persist a message. Returns its id. Inputs are trusted (the
    CLI/schema validates them)."""
    message_id = generate_id()
    message = Message(
        id=message_id,
        schema=1,
        from_project=request.from_project,
        from_session=request.from_session,
        from_uuid=request.from_uuid,
        to_kind=request.to_kind,
        to_value=request.to_value,
        to_location=request.to_partition,
        subject=request.subject,
        sent_at=_now_iso(),
        status="sent",
        read_at=None,
        read_by_uuid=None,
        read_by_session=None,
        claimed_at=None,
        receipt_shown=False,
        thread=request.thread,
        attachments=list(request.attachments),
        body=request.body,
    )
    inbox = ensure_inbox_dir(request.to_partition)
    write_atomic(inbox / message_filename(message_id, request.subject), message)
    return message_id
