# tests/messaging/test_repository_race.py
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import repository as repo
from cc_session_tools.lib.messaging.lock import AlreadyClaimedError
from cc_session_tools.lib.messaging.message import Message


def _aged(mid: str, status: str) -> Message:
    claimed = status == "claimed"
    return Message(
        id=mid, schema=1, from_project="x", from_session="x", from_uuid="s",
        to_kind="project", to_value="alpha", to_location="projects/alpha",
        subject="s", sent_at="2026-06-01T00:00:00Z", status=status,  # type: ignore[arg-type]
        read_at="2026-06-05T00:00:00Z" if status != "sent" else None,
        read_by_uuid="r" if status != "sent" else None,
        read_by_session="r" if status != "sent" else None,
        claimed_at="2026-06-05T00:00:00Z" if claimed else None,
        receipt_shown=False, thread=None, attachments=[], body="b",
    )


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    return tmp_path


def test_concurrent_archive_aged_no_crash_archived_once(root: Path) -> None:
    repo.insert(_aged("20260101T000000Z-0001", "claimed"))
    cutoff = "2026-06-06T00:00:00Z"
    results: list[list[str]] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        try:
            results.append(repo.archive_aged("projects/alpha", cutoff))
        except Exception as exc:  # noqa: BLE001 - captured, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []                                  # no double-unlink crash
    winners = [r for r in results if r]                  # RETURNING gave the id to exactly one
    assert winners == [["20260101T000000Z-0001"]]
    assert repo.get_by_id("20260101T000000Z-0001").status == "archived"


def test_claim_beats_concurrent_retention_on_an_old_unclaimed_message(root: Path) -> None:
    # An old unclaimed message: retention never touches it, so a racing claim always
    # wins and keeps its claimer metadata, with no crash.
    repo.insert(_aged("20260101T000000Z-0002", "sent"))
    errors: list[Exception] = []
    claimed: list[str | None] = []
    barrier = threading.Barrier(2)

    def claimer() -> None:
        barrier.wait()
        try:
            m = repo.claim("20260101T000000Z-0002", "claimer", "beta",
                           "2026-06-20T00:00:00Z")
            claimed.append(m.read_by_uuid)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def retainer() -> None:
        barrier.wait()
        try:
            repo.archive_aged("projects/alpha", "2026-06-06T00:00:00Z")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1, t2 = threading.Thread(target=claimer), threading.Thread(target=retainer)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert errors == []
    assert claimed == ["claimer"]
    final = repo.get_by_id("20260101T000000Z-0002")
    assert final is not None and final.status == "claimed"
    assert final.read_by_uuid == "claimer"


def test_concurrent_claims_of_one_read_message_have_exactly_one_winner(root: Path) -> None:
    repo.insert(_aged("20260101T000000Z-0003", "read"))
    wins: list[str] = []
    losses: list[str] = []
    barrier = threading.Barrier(2)

    def claimer(uuid: str) -> None:
        barrier.wait()
        try:
            repo.claim("20260101T000000Z-0003", uuid, "s", "2026-06-20T00:00:00Z")
            wins.append(uuid)
        except AlreadyClaimedError:
            losses.append(uuid)

    ts = [threading.Thread(target=claimer, args=(u,)) for u in ("a", "b")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert len(wins) == 1 and len(losses) == 1
    final = repo.get_by_id("20260101T000000Z-0003")
    assert final is not None and final.status == "claimed"
    assert final.read_by_uuid == "r"
