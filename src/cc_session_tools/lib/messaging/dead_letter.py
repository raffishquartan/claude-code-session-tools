# src/cc_session_tools/lib/messaging/dead_letter.py
"""Dead-letter sweep: bounce and expire messages that were never read.

Pass 1 classifies expired ``sent`` messages.
Pass 2 sends one coalesced summary bounce per original sender, then archives
the expired originals.

At-least-once guarantee: the summary bounce is written *before* archiving the
originals. A crash between the two means the next sweep re-processes only the
unarchived remainder, never loses a message or drops a bounce.

Loop guard: bounces carry ``from_uuid == SYSTEM_UUID`` (``"__ccmsg_system__"``).
The sweep ages those out silently — never re-bouncing them — so a stale
bounce in an unreachable inbox doesn't accumulate indefinitely.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cc_session_tools.lib.messaging import service
from cc_session_tools.lib.messaging.lock import AlreadyClaimedError, claim_lock
from cc_session_tools.lib.messaging.message import Message, parse, safe_parse, write_atomic
from cc_session_tools.lib.messaging.store import GLOBAL_PARTITION, archive_dir, store_root

SYSTEM_UUID = "__ccmsg_system__"
SYSTEM_SESSION = "ccmsg-system"


@dataclass(frozen=True)
class BounceResult:
    sender_uuid: str
    original_ids: list[str]  # expired originals this result covers
    bounce_id: str | None  # summary bounce id; None for dry-run / aged-out
    action: Literal["bounced", "aged-out", "dry-run"]


def _inbox_message_paths() -> list[Path]:
    root = store_root()
    if not root.is_dir():
        return []
    # Materialised + sorted: robust to partition depth; immune to the bounce we
    # write into _global/inbox mid-sweep. Archive files have parent 'YYYY-MM'.
    return sorted(p for p in root.rglob("*.md") if p.is_file() and p.parent.name == "inbox")


def _age_days(sent_at: str, now: datetime) -> float:
    sent = datetime.strptime(sent_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (now - sent).total_seconds() / 86400.0


def _reason(m: Message, days: int) -> str:
    if m.to_kind == "session":
        return (
            f"to session {m.to_value} — never read within {days} days "
            "(target may have ended)"
        )
    if m.to_kind == "project":
        return f"to project '{m.to_value}' — no session read it within {days} days"
    return f"description-addressed — no session claimed it within {days} days"


def _summary_bounce(
    sender_uuid: str, items: list[tuple[Path, Message]], days: int
) -> service.SendRequest:
    n = len(items)
    body_lines = [f'[{m.id}] "{m.subject}" — {_reason(m, days)}' for _, m in items]
    return service.SendRequest(
        from_project="ccmsg",
        from_session=SYSTEM_SESSION,
        from_uuid=SYSTEM_UUID,
        to_kind="session",
        to_value=sender_uuid,
        to_partition=GLOBAL_PARTITION,
        subject=f"BOUNCE: {n} message(s) expired undelivered",
        body=(
            "The following message(s) you sent expired without being read:\n\n"
            + "\n".join(body_lines)
        ),
        thread=None,
    )


def _archive_in_place(message: Message, path: Path, now: datetime) -> None:
    message.status = "archived"
    dest = archive_dir(message.to_location, now) / path.name
    write_atomic(dest, message)
    if dest != path:
        path.unlink()


def sweep_dead_letters(
    now: datetime,
    *,
    threshold_days: int = 14,
    dry_run: bool = False,
) -> list[BounceResult]:
    results: list[BounceResult] = []
    expired_by_sender: dict[str, list[tuple[Path, Message]]] = {}

    # Pass 1: classify
    for path in _inbox_message_paths():
        m = safe_parse(path)
        if m is None or m.status != "sent":
            continue
        if _age_days(m.sent_at, now) <= threshold_days:
            continue
        if m.from_uuid == SYSTEM_UUID:
            # Age out stale system bounces; never re-bounce them
            if not dry_run:
                _archive_in_place(m, path, now)
            results.append(BounceResult(SYSTEM_UUID, [m.id], None, "aged-out"))
            continue
        expired_by_sender.setdefault(m.from_uuid, []).append((path, m))

    # Pass 2: one summary bounce per sender
    for sender_uuid, items in expired_by_sender.items():
        if dry_run:
            results.append(
                BounceResult(sender_uuid, [m.id for _, m in items], None, "dry-run")
            )
            continue
        live_items: list[tuple[Path, Message]] = []
        for path, m in items:
            try:
                with claim_lock(m.id):
                    try:
                        fresh = parse(path.read_text(encoding="utf-8"))
                    except FileNotFoundError:
                        continue  # archived between scan and lock
                    if fresh.status != "sent":
                        continue  # claimed/read meanwhile
                    live_items.append((path, fresh))
            except AlreadyClaimedError:
                continue
        if not live_items:
            continue
        # Send the bounce first, then archive (at-least-once: a crash here
        # means the next sweep re-processes only the unarchived remainder)
        bounce_id = service.send(_summary_bounce(sender_uuid, live_items, threshold_days))
        for path, fresh in live_items:
            _archive_in_place(fresh, path, now)
        results.append(
            BounceResult(
                sender_uuid,
                [m.id for _, m in live_items],
                bounce_id,
                "bounced",
            )
        )
    return results
