"""Per-session /context-override flag: sessions.db table, not a flag file.

Ported from claude-code-config-sync's ~/.claude/context-overrides/<session_id>
flag file. Backed by the shared sessions.db (context_overrides table),
following doctor_mutes's established shape and degrade-to-safe-default
policy: context_window_warning fires on every Stop event in every open
session, so a read here must never be the thing that crashes someone's turn.
"on" means warnings are silenced for that session; the default (never set, or
any store failure) is False - warnings stay on, matching the bash version's
"fresh session starts with warnings active" behaviour.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cc_session_tools.lib import sessions_db


def get_override(session_id: str, *, path: Path | None = None) -> bool:
    try:
        conn = sessions_db.connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return False
    try:
        row = conn.execute(
            "SELECT state FROM context_overrides WHERE session_id = ?", (session_id,)
        ).fetchone()
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()
    return row is not None and row["state"] == "on"


def set_override(session_id: str, state: str, *, path: Path | None = None) -> None:
    conn = sessions_db.connect(path=path)
    try:
        conn.execute(
            "INSERT INTO context_overrides (session_id, state, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
            (session_id, state, sessions_db._now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
