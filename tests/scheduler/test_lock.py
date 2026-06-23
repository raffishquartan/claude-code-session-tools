# tests/scheduler/test_lock.py
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler.lock import InFlightLockHeld, in_flight_lock


def test_acquire_then_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with in_flight_lock("job-a"):
        pass
    with in_flight_lock("job-a"):  # released, so re-acquire works
        pass


def test_second_concurrent_acquire_same_job_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with in_flight_lock("job-a"):
        with pytest.raises(InFlightLockHeld):
            with in_flight_lock("job-a"):
                pass


def test_different_jobs_lock_independently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with in_flight_lock("job-a"):
        with in_flight_lock("job-b"):  # distinct lock file → both acquire
            pass


def test_stale_lock_is_reclaimed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    # A lock owned by a pid that does not exist (very high pid).
    (tmp_path / ".run.job-a.lock").write_text(json.dumps({"pid": 2_000_000_000, "started": "x"}))
    with in_flight_lock("job-a"):  # should reclaim and succeed
        pass


def test_race_has_exactly_one_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    winners = 0
    guard = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        nonlocal winners
        barrier.wait()
        try:
            with in_flight_lock("job-a"):
                with guard:
                    winners += 1
                import time
                time.sleep(0.02)
        except InFlightLockHeld:
            return

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert winners == 1
