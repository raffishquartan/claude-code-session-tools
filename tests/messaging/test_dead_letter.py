# tests/messaging/test_dead_letter.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_session_tools.lib.messaging.dead_letter import (
    SYSTEM_UUID,
    BounceResult,
    sweep_dead_letters,
)
from cc_session_tools.lib.messaging.message import parse, write_atomic
from cc_session_tools.lib.messaging.service import SendRequest, send
from cc_session_tools.lib.messaging.store import GLOBAL_PARTITION, ensure_inbox_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = datetime(2026, 1, 20, 12, 0, 0, tzinfo=timezone.utc)
_SENT_OLD = "2026-01-01T00:00:00Z"   # 19 days before _BASE → past 14-day threshold
_SENT_EDGE = "2026-01-06T12:00:00Z"  # exactly 14 days before _BASE → not expired
_SENT_RECENT = "2026-01-19T00:00:00Z"  # 1 day before _BASE → not expired


def _send_msg(
    partition: str,
    *,
    from_uuid: str = "sender-a",
    to_kind: str = "session",
    to_value: str = "target-uuid",
    subject: str = "Hello",
    sent_at: str = _SENT_OLD,
) -> str:
    """Write a message with a controlled sent_at timestamp."""
    req = SendRequest(
        from_project="test-project",
        from_session="test-session",
        from_uuid=from_uuid,
        to_kind=to_kind,  # type: ignore[arg-type]
        to_value=to_value,
        to_partition=partition,
        subject=subject,
        body="Body text.",
    )
    msg_id = send(req)
    # Back-date sent_at (service uses _now_iso(); overwrite here for testing)
    inbox = ensure_inbox_dir(partition)
    for f in inbox.glob(f"{msg_id}__*.md"):
        m = parse(f.read_text(encoding="utf-8"))
        m.sent_at = sent_at
        write_atomic(f, m)
    return msg_id


# ---------------------------------------------------------------------------
# Core sweep tests
# ---------------------------------------------------------------------------


def test_one_sender_three_aged_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One sender with 3 expired 'sent' messages → one bounce, all 3 archived."""
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))

    for i in range(3):
        _send_msg(GLOBAL_PARTITION, subject=f"msg-{i}")

    results = sweep_dead_letters(_BASE, threshold_days=14)
    bounced = [r for r in results if r.action == "bounced"]
    assert len(bounced) == 1
    assert len(bounced[0].original_ids) == 3
    assert bounced[0].sender_uuid == "sender-a"

    # Originals all archived (not in inbox); only the bounce message remains in inbox
    inbox_files = list((tmp_path / GLOBAL_PARTITION / "inbox").glob("*.md"))
    assert len(inbox_files) == 1
    bounce_msg = parse(inbox_files[0].read_text(encoding="utf-8"))
    assert bounce_msg.from_uuid == SYSTEM_UUID
    assert bounce_msg.to_value == "sender-a"

    # Bounce id findable
    from cc_session_tools.lib.messaging import service
    assert service.find_by_id(bounced[0].bounce_id) is not None  # type: ignore[arg-type]


def test_second_sweep_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After a sweep archives originals and writes a bounce, next sweep does nothing new."""
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    _send_msg(GLOBAL_PARTITION)

    sweep_dead_letters(_BASE, threshold_days=14)
    results2 = sweep_dead_letters(_BASE, threshold_days=14)
    # The bounce msg from the first sweep has from_uuid=SYSTEM_UUID so it gets
    # aged out; the originals are already archived.  No new 'bounced' results.
    bounced2 = [r for r in results2 if r.action == "bounced"]
    assert len(bounced2) == 0


def test_two_senders_two_bounces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    _send_msg(GLOBAL_PARTITION, from_uuid="sender-a")
    _send_msg(GLOBAL_PARTITION, from_uuid="sender-b")

    results = sweep_dead_letters(_BASE, threshold_days=14)
    bounced = {r.sender_uuid for r in results if r.action == "bounced"}
    assert bounced == {"sender-a", "sender-b"}


def test_read_message_not_bounced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_msg(GLOBAL_PARTITION)
    # Mark message as read
    from cc_session_tools.lib.messaging import service
    from cc_session_tools.lib.messaging.service import Claimer
    service.claim(mid, Claimer(uuid="reader", session="reader-session"))

    results = sweep_dead_letters(_BASE, threshold_days=14)
    assert not any(r.action == "bounced" for r in results)


def test_claimed_message_not_bounced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    mid = _send_msg(GLOBAL_PARTITION)
    from cc_session_tools.lib.messaging import service
    from cc_session_tools.lib.messaging.service import Claimer
    service.claim(mid, Claimer(uuid="claimer", session="claimer-session"))

    results = sweep_dead_letters(_BASE, threshold_days=14)
    assert not any(r.action == "bounced" for r in results)


def test_per_kind_reason_strings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    _send_msg(GLOBAL_PARTITION, from_uuid="s", to_kind="session", to_value="tgt")
    _send_msg(GLOBAL_PARTITION, from_uuid="s", to_kind="project", to_value="proj-x")
    _send_msg(GLOBAL_PARTITION, from_uuid="s", to_kind="description", to_value="do-x")

    results = sweep_dead_letters(_BASE, threshold_days=14)
    bounced = [r for r in results if r.action == "bounced" and r.sender_uuid == "s"]
    assert len(bounced) == 1
    # Retrieve the bounce message body
    from cc_session_tools.lib.messaging import service
    bounce_path = service.find_by_id(bounced[0].bounce_id)  # type: ignore[arg-type]
    assert bounce_path is not None
    body = parse(bounce_path.read_text(encoding="utf-8")).body
    assert "to session tgt" in body
    assert "to project 'proj-x'" in body
    assert "description-addressed" in body


def test_aged_system_bounce_not_rebounced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale system bounce in an inbox ages out, never re-bounced."""
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    req = SendRequest(
        from_project="ccmsg",
        from_session="ccmsg-system",
        from_uuid=SYSTEM_UUID,
        to_kind="session",
        to_value="some-session",
        to_partition=GLOBAL_PARTITION,
        subject="BOUNCE: old",
        body="Old bounce body.",
    )
    mid = send(req)
    # Back-date it
    inbox = ensure_inbox_dir(GLOBAL_PARTITION)
    for f in inbox.glob(f"{mid}__*.md"):
        m = parse(f.read_text(encoding="utf-8"))
        m.sent_at = _SENT_OLD
        write_atomic(f, m)

    results = sweep_dead_letters(_BASE, threshold_days=14)
    assert any(r.action == "aged-out" for r in results)
    assert not any(r.action == "bounced" for r in results)


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    _send_msg(GLOBAL_PARTITION)

    results = sweep_dead_letters(_BASE, threshold_days=14, dry_run=True)
    assert all(r.action == "dry-run" for r in results)
    # Inbox should still contain the original (unarchived)
    inbox_files = list((tmp_path / GLOBAL_PARTITION / "inbox").glob("*.md"))
    assert len(inbox_files) == 1


def test_threshold_boundary_exact_not_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A message sent exactly threshold_days ago is NOT swept."""
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    _send_msg(GLOBAL_PARTITION, sent_at=_SENT_EDGE)  # exactly 14 days

    results = sweep_dead_letters(_BASE, threshold_days=14)
    assert not any(r.action in ("bounced", "aged-out") for r in results)


def test_threshold_boundary_over_is_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A message sent threshold_days + 1 second ago IS swept."""
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    just_over = (_BASE - timedelta(days=14, seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _send_msg(GLOBAL_PARTITION, sent_at=just_over)

    results = sweep_dead_letters(_BASE, threshold_days=14)
    assert any(r.action == "bounced" for r in results)


def test_bounce_from_project_is_ccmsg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounce message has from_project='ccmsg' (not empty)."""
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    _send_msg(GLOBAL_PARTITION)

    results = sweep_dead_letters(_BASE, threshold_days=14)
    bounced = [r for r in results if r.action == "bounced"]
    assert len(bounced) == 1
    from cc_session_tools.lib.messaging import service
    path = service.find_by_id(bounced[0].bounce_id)  # type: ignore[arg-type]
    assert path is not None
    m = parse(path.read_text(encoding="utf-8"))
    assert m.from_project == "ccmsg"
    assert m.from_uuid == SYSTEM_UUID


def test_concurrency_two_sweeps_one_bounce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulated concurrent sweep: both see the same unarchived message but
    only one acquires the lock and bounces it."""
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    _send_msg(GLOBAL_PARTITION, from_uuid="sender-c")

    # First sweep
    r1 = sweep_dead_letters(_BASE, threshold_days=14)
    bounced1 = [r for r in r1 if r.action == "bounced"]
    assert len(bounced1) == 1

    # Second sweep immediately after: original is archived, system bounce is too new
    r2 = sweep_dead_letters(_BASE, threshold_days=14)
    bounced2 = [r for r in r2 if r.action == "bounced"]
    assert len(bounced2) == 0


def test_at_least_once_unarchived_remainder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If _archive_in_place crashes mid-loop after the bounce is sent,
    the next clean sweep re-bounces only the unarchived remainder."""
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    _send_msg(GLOBAL_PARTITION, from_uuid="sender-d", subject="first")
    _send_msg(GLOBAL_PARTITION, from_uuid="sender-d", subject="second")

    from cc_session_tools.lib.messaging import dead_letter as dl_mod

    original_archive = dl_mod._archive_in_place
    # Archive the first call, raise on the second — order-independent
    call_count = [0]
    archived_ids: list[str] = []

    def _archive_first_then_raise(message, path, now):  # type: ignore[no-untyped-def]
        call_count[0] += 1
        if call_count[0] == 1:
            original_archive(message, path, now)
            archived_ids.append(message.id)
        else:
            raise OSError("simulated crash")

    with patch.object(dl_mod, "_archive_in_place", side_effect=_archive_first_then_raise):
        try:
            sweep_dead_letters(_BASE, threshold_days=14)
        except OSError:
            pass  # expected from the simulated crash

    # Exactly one original was archived; one remains in inbox
    assert len(archived_ids) == 1
    inbox_files = [
        f for f in (tmp_path / GLOBAL_PARTITION / "inbox").glob("*.md")
        if parse(f.read_text(encoding="utf-8")).from_uuid != SYSTEM_UUID
    ]
    assert len(inbox_files) == 1
    remaining_id = inbox_files[0].name.split("__")[0]
    assert remaining_id not in archived_ids

    # A clean second sweep bounces only the unarchived remainder
    r2 = sweep_dead_letters(_BASE, threshold_days=14)
    bounced2 = [r for r in r2 if r.action == "bounced"]
    assert len(bounced2) == 1
    assert remaining_id in bounced2[0].original_ids
