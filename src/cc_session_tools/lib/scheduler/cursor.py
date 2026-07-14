"""Per-session surfacing cursor (§9.3), backed by the `cursors` table in
ccsched.db. offset = count of catch-up ledger rows already surfaced to this
session. Per-session by design; cross-session dedup is a non-goal. (The ledger
itself is still fires.jsonl until Phase 5, so this offset keeps its exact
current meaning.)"""
from __future__ import annotations

from cc_session_tools.lib.scheduler import ledger, store


def read_cursor(uuid: str) -> int:
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT offset FROM cursors WHERE session_uuid=?", (uuid,)
        ).fetchone()
    finally:
        conn.close()
    return int(row["offset"]) if row is not None else 0


def write_cursor(uuid: str, offset: int) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "INSERT INTO cursors (session_uuid, offset) VALUES (?, ?) "
            "ON CONFLICT(session_uuid) DO UPDATE SET offset=excluded.offset",
            (uuid, offset),
        )
        conn.commit()
    finally:
        conn.close()


def seed_new_session(uuid: str) -> None:
    """Seed this session's cursor at the current end of the ledger if it has none
    yet, so its first digest reflects only activity from this point forward — not
    the entire pre-existing ledger. INSERT OR IGNORE makes it idempotent."""
    conn = store.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO cursors (session_uuid, offset) VALUES (?, ?)",
            (uuid, ledger.current_offset()),
        )
        conn.commit()
    finally:
        conn.close()
