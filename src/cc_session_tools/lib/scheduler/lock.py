# src/cc_session_tools/lib/scheduler/lock.py
"""Per-job in-flight lock at <scheduler-dir>/.run.<job-id>.lock.

The mechanism (O_CREAT|O_EXCL create, dead-holder reclaim, in-process
threading.Lock guarding the reclaim TOCTOU window) lives in lib/proc_lock.py,
shared with install_sync's auto-apply lock. This module owns the per-job path
derivation and the InFlightLockHeld type.

This per-job lock is the sole overlap-prevention guarantee (§10): there is no
global sweep lock, so two sessions launching the same owed job is harmless -
only the lock winner runs.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cc_session_tools.lib.proc_lock import LockHeld, exclusive_lock, pid_alive
from cc_session_tools.lib.scheduler.store import scheduler_dir

__all__ = ["InFlightLockHeld", "in_flight_lock", "pid_alive"]


class InFlightLockHeld(LockHeld):
    """Raised when a job's in-flight lock is held by a live process."""


def _lock_path(job_id: str) -> Path:
    return scheduler_dir() / f".run.{job_id}.lock"


@contextmanager
def in_flight_lock(job_id: str) -> Iterator[None]:
    with exclusive_lock(
        _lock_path(job_id),
        held_exc=InFlightLockHeld,
        label=f"in-flight lock for {job_id!r}",
    ):
        yield
