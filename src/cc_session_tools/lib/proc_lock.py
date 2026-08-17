# src/cc_session_tools/lib/proc_lock.py
"""Exclusive, try-once inter-process lock file. Never waits.

Atomicity from os.open(O_CREAT|O_EXCL): exactly one caller creates the file for
a given path. A contender raises LockHeld unless the recorded holder pid is
dead, in which case the stale lock is reclaimed. Callers that lose are expected
to skip their work, not to retry or block.

An in-process threading.Lock, keyed on the lock path, guards the O_EXCL
create+check sequence to close a TOCTOU window: without it, a stale-lock
reclaim (FileExistsError -> holder-dead check -> unlink -> re-create) can
interleave with another thread's create attempt inside the same process,
producing two simultaneous holders.

Two callers share this: scheduler/lock.py's per-job in-flight lock, and
install_sync.ensure_synced's auto-apply lock.
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Keyed on str(path) rather than on any caller-level id: the path is the thing
# O_EXCL actually contends on, so two callers that resolve to the same file
# always share a thread lock and two that don't never do.
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


class LockHeld(RuntimeError):
    """Raised when the lock file is held by a live process (or another thread)."""


def _thread_lock_for(key: str) -> threading.Lock:
    with _thread_locks_guard:
        if key not in _thread_locks:
            _thread_locks[key] = threading.Lock()
        return _thread_locks[key]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours to signal
    return True


def _read_holder(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    pid = data.get("pid")
    return int(pid) if isinstance(pid, int) else None


def _try_create(path: Path) -> int:
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    payload = json.dumps(
        {"pid": os.getpid(), "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    )
    os.write(fd, payload.encode())
    return fd


@contextmanager
def exclusive_lock(
    path: Path,
    *,
    held_exc: type[LockHeld] = LockHeld,
    label: str | None = None,
) -> Iterator[None]:
    """Hold `path` exclusively for the body, or raise `held_exc` immediately.

    `held_exc`/`label` exist so a caller can keep its own exception type and
    message wording (scheduler/lock.py's InFlightLockHeld, "in-flight lock for
    'job-a' held by live pid 123") without reimplementing the mechanism.
    """
    what = label or str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tlock = _thread_lock_for(str(path))
    if not tlock.acquire(blocking=False):
        raise held_exc(f"{what} held by another thread")
    try:
        try:
            fd = _try_create(path)
        except FileExistsError:
            holder = _read_holder(path)
            if holder is not None and pid_alive(holder):
                raise held_exc(f"{what} held by live pid {holder}") from None
            path.unlink(missing_ok=True)  # stale -> reclaim
            try:
                fd = _try_create(path)
            except FileExistsError:
                # Another process reclaimed and reacquired between our unlink
                # and our create - we lose this race: treat as held.
                raise held_exc(f"{what} re-acquired concurrently") from None
        try:
            yield
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)
    finally:
        tlock.release()
