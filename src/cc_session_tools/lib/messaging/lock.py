# src/cc_session_tools/lib/messaging/lock.py
"""First-claim-wins lock for description-addressed messages.

Atomicity comes from ``os.open(O_CREAT | O_EXCL)``: exactly one caller creates
the sidecar lock file; everyone else sees ``FileExistsError`` and is told the
message is already claimed. Locks live under ``<store>/.locks/``.

The lock is released in a ``finally`` on every normal or exceptional exit. The
one uncovered failure mode is a hard kill (SIGKILL, power loss) between acquiring
and releasing: that orphans the ``.lock`` sidecar and the message stays
unclaimable until the stale file is removed by hand. Claims are short-lived, so
this is rare; a future enhancement could expire locks by age."""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cc_session_tools.lib.messaging.store import store_root


class AlreadyClaimedError(Exception):
    """Raised when a lock for a message id is already held by another caller."""


def _locks_dir() -> Path:
    d = store_root() / ".locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def claim_lock(message_id: str) -> Iterator[None]:
    lock_path = _locks_dir() / f"{message_id}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise AlreadyClaimedError(message_id) from exc
    try:
        yield
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)
