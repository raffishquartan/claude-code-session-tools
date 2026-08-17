"""Tests for cc_session_tools.lib.proc_lock - the shared exclusive lock file."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from cc_session_tools.lib.proc_lock import LockHeld, exclusive_lock


def test_acquire_then_release_then_reacquire(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "x.lock"
    with exclusive_lock(path):
        assert path.exists()
    assert not path.exists()
    with exclusive_lock(path):  # released, so re-acquire works
        pass


def test_creates_missing_parent_directory(tmp_path: Path) -> None:
    """paths.data_home() is not guaranteed to exist on a fresh machine, and
    install_sync's lock lives directly inside it."""
    path = tmp_path / "does" / "not" / "exist" / "x.lock"
    with exclusive_lock(path):
        assert path.exists()


def test_second_acquire_raises_lock_held(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    with exclusive_lock(path):
        with pytest.raises(LockHeld):
            with exclusive_lock(path):
                pass


def test_distinct_paths_lock_independently(tmp_path: Path) -> None:
    with exclusive_lock(tmp_path / "a.lock"):
        with exclusive_lock(tmp_path / "b.lock"):
            pass


def test_stale_lock_naming_a_dead_pid_is_reclaimed(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    path.write_text(json.dumps({"pid": 2_000_000_000, "started": "x"}))
    with exclusive_lock(path):  # reclaimed
        assert json.loads(path.read_text())["pid"] == os.getpid()


def test_lock_file_records_the_holder_pid(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    with exclusive_lock(path):
        assert json.loads(path.read_text())["pid"] == os.getpid()


def test_race_has_exactly_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    winners = 0
    guard = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        nonlocal winners
        barrier.wait()
        try:
            with exclusive_lock(path):
                with guard:
                    winners += 1
                import time
                time.sleep(0.02)
        except LockHeld:
            pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert winners == 1


def test_custom_exception_type_and_label(tmp_path: Path) -> None:
    """scheduler/lock.py needs to keep raising InFlightLockHeld with its own
    message wording - the extraction must not flatten every caller onto the
    generic type."""
    class Mine(LockHeld):
        pass

    path = tmp_path / "x.lock"
    with exclusive_lock(path, held_exc=Mine, label="widget 'w1'"):
        with pytest.raises(Mine) as exc:
            with exclusive_lock(path, held_exc=Mine, label="widget 'w1'"):
                pass
    assert "widget 'w1'" in str(exc.value)
