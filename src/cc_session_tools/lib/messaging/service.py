# src/cc_session_tools/lib/messaging/service.py
"""Shared messaging service used by both the ccmsg CLI and the delivery hook.

This module holds business logic; argparse validation stays in the CLI."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib.messaging.message import (
    Message,
    Status,
    ToKind,
    parse,
    write_atomic,
)
from cc_session_tools.lib.messaging.store import (
    ensure_inbox_dir,
    generate_id,
    message_filename,
    store_root,
)

logger = logging.getLogger(__name__)


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


def _iter_message_files() -> list[Path]:
    root = store_root()
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file())


def find_by_id(message_id: str) -> Path | None:
    """Scan inbox and archive across all partitions for a message by id."""
    for path in _iter_message_files():
        if path.name.startswith(f"{message_id}__"):
            return path
    return None


def read_one(message_id: str) -> Message | None:
    """Return the parsed ``Message`` for *message_id*, or ``None`` if not found.

    A found-but-corrupt file raises ``ValueError`` (from ``parse``) so the caller
    can report that specific id as unreadable rather than silently "not found"."""
    path = find_by_id(message_id)
    return parse(path.read_text(encoding="utf-8")) if path is not None else None


def _safe_parse(path: Path) -> Message | None:
    """Parse a store file, returning ``None`` (and logging) for a malformed one so
    that a single stale/hand-edited file never aborts a whole sweep or listing."""
    try:
        return parse(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        logger.warning("skipping unreadable message file: %s", path)
        return None


@dataclass(frozen=True)
class MessageRow:
    id: str
    status: Status
    to_kind: ToKind
    to_value: str
    from_session: str
    subject: str


def list_messages(
    *,
    status: str | None = None,
    partition: str | None = None,
    from_uuid: str | None = None,
) -> list[MessageRow]:
    """Return compact rows, optionally filtered by status, partition, or sender uuid."""
    rows: list[MessageRow] = []
    for path in _iter_message_files():
        m = _safe_parse(path)
        if m is None:
            continue
        if status is not None and m.status != status:
            continue
        if partition is not None and m.to_location != partition:
            continue
        if from_uuid is not None and m.from_uuid != from_uuid:
            continue
        rows.append(MessageRow(
            id=m.id,
            status=m.status,
            to_kind=m.to_kind,
            to_value=m.to_value,
            from_session=m.from_session,
            subject=m.subject,
        ))
    return rows
