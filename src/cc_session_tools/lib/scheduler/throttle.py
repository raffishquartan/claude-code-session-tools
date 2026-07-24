"""Per-session reconcile throttle (§13), backed by the `reconcile_throttle`
table in ccsched.db. A single UPSERTed timestamp row per session gates
UserPromptSubmit-triggered reconciles to at most once per throttle window. This
replaces the old .reconcile.<uuid>.ts flat file — the ONE non-atomic write in
the whole subsystem (plain write_text, no tmp-swap) — with a single-row write."""
from __future__ import annotations

from datetime import datetime

from cc_session_tools.lib.scheduler import state, store


def read_last_reconciled(uuid: str) -> datetime | None:
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT last_reconciled_at FROM reconcile_throttle WHERE session_uuid=?",
            (uuid,),
        ).fetchone()
    finally:
        conn.close()
    return state.parse_ts_or_none(row["last_reconciled_at"]) if row is not None else None


def stamp_reconciled(uuid: str, now: datetime) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "INSERT INTO reconcile_throttle (session_uuid, last_reconciled_at) "
            "VALUES (?, ?) ON CONFLICT(session_uuid) DO UPDATE SET "
            "last_reconciled_at=excluded.last_reconciled_at",
            (uuid, state.format_ts(now)),
        )
        conn.commit()
    finally:
        conn.close()
