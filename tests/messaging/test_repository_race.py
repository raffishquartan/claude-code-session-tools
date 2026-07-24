# tests/messaging/test_repository_race.py
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import repository as repo
from cc_session_tools.lib.messaging.lock import AlreadyClaimedError
from cc_session_tools.lib.messaging.message import Message


def _aged_read(mid: str) -> Message:
    return Message(
        id=mid, schema=1, from_project="x", from_session="x", from_uuid="s",
        to_kind="project", to_value="alpha", to_location="projects/alpha",
        subject="s", sent_at="2026-06-01T00:00:00Z", status="read",
        read_at="2026-06-05T00:00:00Z", read_by_uuid="r", read_by_session="r",
        claimed_at=None, receipt_shown=False, thread=None, attachments=[], body="b",
    )


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    return tmp_path


def test_concurrent_archive_aged_no_crash_archived_once(root: Path) -> None:
    repo.insert(_aged_read("20260101T000000Z-0001"))
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


def test_claim_and_retention_race_preserves_claim_metadata(root: Path) -> None:
    # A message that is claimable now AND aged-read: whichever wins, no crash and
    # no lost metadata. Insert as aged-read so retention is eligible; a claimer
    # races it. Either the claim wins (message ends claimed, metadata intact) or
    # retention wins first (claim then sees a terminal status -> AlreadyClaimed).
    repo.insert(_aged_read("20260101T000000Z-0002"))
    outcomes: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def claimer() -> None:
        barrier.wait()
        try:
            m = repo.claim("20260101T000000Z-0002", "claimer", "beta",
                           "2026-06-20T00:00:00Z")
            outcomes.append(f"claimed:{m.read_by_uuid}")
        except AlreadyClaimedError:
            outcomes.append("already")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def retainer() -> None:
        barrier.wait()
        try:
            repo.archive_aged("projects/alpha", "2026-06-06T00:00:00Z")
            outcomes.append("archived-sweep")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1, t2 = threading.Thread(target=claimer), threading.Thread(target=retainer)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert errors == []
    final = repo.get_by_id("20260101T000000Z-0002")
    assert final is not None
    # Metadata is never partially lost: a claimed row keeps its claimer; an
    # archived-without-claim row keeps its original reader.
    if final.status == "claimed":
        assert final.read_by_uuid == "claimer"
    else:
        assert final.status == "archived"
        assert final.read_by_uuid in ("claimer", "r")
