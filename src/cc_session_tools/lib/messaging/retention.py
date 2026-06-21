# src/cc_session_tools/lib/messaging/retention.py
"""Opportunistic retention: archive read/claimed messages older than 14 days.

Archiving is a move (never a delete). Unread messages never expire. Called from
``deliver`` with a bounded per-sweep cost."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cc_session_tools.lib.messaging.message import Message, safe_parse, write_atomic
from cc_session_tools.lib.messaging.store import archive_dir, ensure_inbox_dir

_RETENTION_DAYS = 14
_ARCHIVABLE = ("read", "claimed")


def _parse_stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _settled_at(message: Message) -> str | None:
    return message.claimed_at or message.read_at


def archive_old(partition: str, now: datetime) -> list[str]:
    """Archive eligible messages in ``partition``'s inbox. Returns the ids
    archived (sorted by encounter order)."""
    inbox = ensure_inbox_dir(partition)
    cutoff = now - timedelta(days=_RETENTION_DAYS)
    archived: list[str] = []
    for path in sorted(inbox.glob("*.md")):
        message = safe_parse(path)
        if message is None:
            continue
        if message.status not in _ARCHIVABLE:
            continue
        stamp = _settled_at(message)
        if stamp is None:
            continue
        settled = _parse_stamp(stamp)
        if settled > cutoff:
            continue
        message.status = "archived"
        dest = archive_dir(partition, settled) / path.name
        write_atomic(dest, message)
        path.unlink()
        archived.append(message.id)
    return archived
