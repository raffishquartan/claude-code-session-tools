# src/cc_session_tools/lib/messaging/service.py
"""Shared messaging service used by both the ccmsg CLI and the delivery hook.

This module holds business logic; argparse validation stays in the CLI."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cc_session_tools.lib.messaging import cursor as cursor_mod
from cc_session_tools.lib.messaging import repository
from cc_session_tools.lib.messaging import retention
from cc_session_tools.lib.messaging.addressing import (
    MatchKind,
    SessionContext,
    targets,
)
from cc_session_tools.lib.messaging.lock import AlreadyClaimedError, claim_lock
from cc_session_tools.lib.messaging.message import (
    Message,
    Status,
    ToKind,
)
from cc_session_tools.lib.messaging.repository import MessageNotFoundError
from cc_session_tools.lib.messaging.store import (
    GLOBAL_PARTITION,
    generate_id,
)

logger = logging.getLogger(__name__)

DeliverMode = Literal["full", "incremental"]


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
    repository.insert(message)
    return message_id


@dataclass(frozen=True)
class Claimer:
    uuid: str
    session: str


def find_by_id(message_id: str) -> Message | None:
    """Single indexed primary-key lookup (was a full rglob scan)."""
    return repository.get_by_id(message_id)


def claim(message_id: str, claimer: Claimer) -> Message:
    """First-claim-wins. The file-based claim_lock (R4, kept outside the DB) is
    the coarse envelope; repository.claim provides the atomic state transition."""
    now = _now_iso()
    with claim_lock(message_id):
        return repository.claim(message_id, claimer.uuid, claimer.session, now)


def archive(message_id: str, now: datetime) -> Message:  # noqa: ARG001 - kept for CLI signature stability
    """Manual archive. Acquires claim_lock so it cannot race a concurrent claim
    (R4), then flips status atomically."""
    with claim_lock(message_id):
        return repository.archive_one(message_id)


def read_one(message_id: str) -> Message | None:
    return repository.get_by_id(message_id)


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
    return [
        MessageRow(
            id=m.id, status=m.status, to_kind=m.to_kind, to_value=m.to_value,
            from_session=m.from_session, subject=m.subject,
        )
        for m in repository.list_rows(status=status, partition=partition, from_uuid=from_uuid)
    ]


def _relative_age(sent_at: str, now: datetime) -> str:
    sent = datetime.strptime(sent_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    minutes = int((now - sent).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _digest_line(message: Message, now: datetime) -> str:
    return (
        f"[{message.id}] from {message.from_session} ({message.from_project}) · "
        f"{message.subject} · {_relative_age(message.sent_at, now)}"
    )


def _swept_partitions(ctx: SessionContext) -> list[str]:
    parts = [ctx.partition]
    if GLOBAL_PARTITION not in parts:
        parts.append(GLOBAL_PARTITION)
    return parts


def deliver(ctx: SessionContext, *, mode: DeliverMode) -> str:
    """Sweep relevant partitions, auto-read recipient messages, surface
    description proposals, emit receipts, run opportunistic retention, and
    return a compact digest (empty if nothing to show). ``mode`` is advisory
    (``full`` vs ``incremental``); the cursor bounds both identically."""
    now = datetime.now(timezone.utc)
    cur = cursor_mod.load(ctx.uuid)
    inbound: list[str] = []
    proposals: list[str] = []

    for partition in _swept_partitions(ctx):
        inbox = ensure_inbox_dir(partition)
        for path in sorted(inbox.glob("*.md")):
            message = safe_parse(path)
            if message is None:
                continue
            if not cursor_mod.is_new(message, cur):
                continue
            kind = targets(message, ctx)
            if kind is MatchKind.RECIPIENT:
                message.status = "read"
                message.read_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
                message.read_by_uuid = ctx.uuid
                # SessionContext carries no display tag (the SessionStart/
                # UserPromptSubmit stdin does not include one), so auto-read
                # stamps the project label here. The sender's receipt will
                # therefore read "by <project>" for auto-read and "by
                # <session-tag>" for an explicit claim. This is intentional —
                # do NOT "fix" it by inventing a tag. If a true session tag is
                # ever wanted in receipts, add a ``session_tag`` field to
                # SessionContext threaded from the hook stdin.
                message.read_by_session = ctx.project
                write_atomic(path, message)
                inbound.append(_digest_line(message, now))
                cur = cursor_mod.advance(cur, message)
            elif kind is MatchKind.CANDIDATE:
                # A description proposal is surfaced to this session once, then
                # the cursor advances so it is not re-nudged every prompt. Other
                # sessions still see it (their own cursors are independent), so
                # first-claim-wins is unaffected.
                proposals.append(_digest_line(message, now))
                cur = cursor_mod.advance(cur, message)
        retention.archive_old(partition, now)

    cursor_mod.save(ctx.uuid, cur)
    receipts = _collect_receipts(ctx, now)

    return _format_digest(inbound, proposals, receipts)


def _collect_receipts(ctx: SessionContext, now: datetime) -> list[str]:
    lines: list[str] = []
    for path in _iter_message_files():
        message = safe_parse(path)
        if message is None:
            continue
        if message.from_uuid != ctx.uuid:
            continue
        if message.status not in ("read", "claimed"):
            continue
        if message.receipt_shown:
            continue
        who = message.read_by_session or "a session"
        lines.append(
            f'✓ read: "{message.subject}" by {who} '
            f"({_relative_age(message.sent_at, now)}) [{message.id}]"
        )
        message.receipt_shown = True
        write_atomic(path, message)
    return lines


def _format_digest(
    inbound: list[str], proposals: list[str], receipts: list[str]
) -> str:
    if not (inbound or proposals or receipts):
        return ""
    out: list[str] = ["[cc-messages] You have inter-session messages:"]
    out.extend(inbound)
    if proposals:
        out.append(
            "Unclaimed messages addressed by description "
            "(claim if this session fits):"
        )
        out.extend(proposals)
    if receipts:
        out.extend(receipts)
    out.append(
        "Read a body with `ccmsg read <id>`. To take a description-addressed "
        "message, confirm with the user then `ccmsg claim <id>`."
    )
    return "\n".join(out)
