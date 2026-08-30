"""Non-blocking "is someone else writing to this .db right now" check (spec: "Process safety").
Deliberately narrower than the superseded 2026-08-02 spec's whole-machine process check — this
only ever answers for one specific file, compatible with automatic (hook/cron) triggers."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def is_locked(db_path: Path) -> bool:
    """True iff some other connection currently holds (or is contending for) the write lock on
    db_path, right now. timeout=0 disables sqlite3's own busy-retry — without it, BEGIN IMMEDIATE
    would silently block for the driver's default timeout instead of answering instantly, which
    is the one property this check exists to provide (spec: "a non-blocking exclusive check ...
    with a near-zero busy-timeout")."""
    conn = sqlite3.connect(db_path, timeout=0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
        return False
    except sqlite3.OperationalError:
        return True
    finally:
        conn.close()
