# tests/messaging/test_lock.py
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging.lock import AlreadyClaimedError, claim_lock


def test_first_claim_succeeds_then_releases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    with claim_lock("20260620T000000Z-0001"):
        pass
    # Lock released on exit, so a second claim also succeeds.
    with claim_lock("20260620T000000Z-0001"):
        pass


def test_second_concurrent_claim_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    with claim_lock("20260620T000000Z-0001"):
        with pytest.raises(AlreadyClaimedError):
            with claim_lock("20260620T000000Z-0001"):
                pass


def test_race_has_exactly_one_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    winners = 0
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        nonlocal winners
        barrier.wait()
        try:
            with claim_lock("race-id"):
                with lock:
                    winners += 1
                # Hold briefly so contenders overlap.
                time.sleep(0.02)
        except AlreadyClaimedError:
            return

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert winners == 1
