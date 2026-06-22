# src/cc_session_tools/lib/scheduler/lock.py
"""Per-job in-flight lock at <scheduler-dir>/.run.<job-id>.lock.

Atomicity from os.open(O_CREAT|O_EXCL): exactly one caller creates the file for
a given job id. A contender raises InFlightLockHeld unless the recorded holder
pid is dead, in which case the stale lock is reclaimed. This per-job lock is the
sole overlap-prevention guarantee (§10): there is no global sweep lock, so two
sessions launching the same owed job is harmless — only the lock winner runs."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib.scheduler.state import scheduler_dir


class InFlightLockHeld(RuntimeError):
    """Raised when a job's in-flight lock is held by a live process."""


def _lock_path(job_id: str) -> Path:
    return scheduler_dir() / f".run.{job_id}.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours to signal
    return True


def _try_create(path: Path) -> int:
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    payload = json.dumps(
        {"pid": os.getpid(), "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    )
    os.write(fd, payload.encode())
    return fd


@contextmanager
def in_flight_lock(job_id: str) -> Iterator[None]:
    scheduler_dir().mkdir(parents=True, exist_ok=True)
    path = _lock_path(job_id)
    try:
        fd = _try_create(path)
    except FileExistsError:
        holder = _read_holder(path)
        if holder is not None and _pid_alive(holder):
            raise InFlightLockHeld(f"in-flight lock for {job_id!r} held by live pid {holder}")
        path.unlink(missing_ok=True)  # stale → reclaim
        fd = _try_create(path)
    try:
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def _read_holder(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    pid = data.get("pid")
    return int(pid) if isinstance(pid, int) else None
