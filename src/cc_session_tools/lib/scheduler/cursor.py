"""Per-session surfacing cursor (§9.3), backed by the `cursors` table in
ccsched.db. offset = highest catch-up ledger row id already surfaced to this
session (a monotonic telemetry.db catchup_events.id, not a row count). Per-
session by design; cross-session dedup is a non-goal."""
from __future__ import annotations

from datetime import datetime, timezone

from cc_session_tools.lib.scheduler import ledger, store


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        now_iso = _now_iso()
        conn.execute(
            "INSERT INTO cursors (session_uuid, offset, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(session_uuid) DO UPDATE SET "
            "offset=excluded.offset, updated_at=excluded.updated_at",
            (uuid, offset, now_iso, now_iso),
        )
        conn.commit()
    finally:
        conn.close()


def advance_all_cursors_to(offset: int) -> int:
    """Move every session cursor at or below ``offset`` up to it. Returns the
    number of cursors moved.

    Used after the one-shot fires.jsonl import appends historical catch-up
    rows. Those rows are older than everything already in the ledger, but
    AUTOINCREMENT gives them the *highest* ids, so `read_since`'s `WHERE id >
    ?` would serve imported history to every existing session as brand-new
    activity — surfacing stale digests and re-reaping jobs that already ran.
    Advancing the cursors past the import applies exactly the rule
    `seed_new_session` already applies to a new session: pre-existing history
    is not news.

    A no-op when ccsched.db does not exist yet (no sessions, so no cursors);
    it is not this function's job to create the scheduler store.
    """
    if not store.db_path().exists():
        return 0
    conn = store.connect()
    try:
        cur = conn.execute(
            "UPDATE cursors SET offset = ?, updated_at = ? WHERE offset < ?",
            (offset, _now_iso(), offset),
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def seed_new_session(uuid: str) -> None:
    """Seed this session's cursor at the current end of the ledger if it has none
    yet, so its first digest reflects only activity from this point forward — not
    the entire pre-existing ledger. INSERT OR IGNORE makes it idempotent."""
    conn = store.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO cursors (session_uuid, offset, created_at) "
            "VALUES (?, ?, ?)",
            (uuid, ledger.current_offset(), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
