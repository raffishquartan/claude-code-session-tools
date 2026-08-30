"""Reads/writes the pdata_meta table (spec: "The vector clock") for one already-open connection.
Pure vector math lives in vector_clock.py — this module is the only place that touches SQL for it.
Caller owns the transaction (wrap writes in repository._immediate), matching every other write
path in this package."""
from __future__ import annotations

import sqlite3
import time

from cc_session_tools.lib.pdata import vector_clock


def read_vector(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT machine_id, revision FROM pdata_meta").fetchall()
    return {row["machine_id"]: row["revision"] for row in rows}


def write_vector(conn: sqlite3.Connection, vector: dict[str, int], *, updated_at: int) -> None:
    """Replaces the entire table's contents — the caller already has the full merged/bumped
    vector it wants in hand (vector_clock.merge()/bump_own() are pure dict operations), so this
    is always a full overwrite, never a partial update."""
    conn.execute("DELETE FROM pdata_meta")
    conn.executemany(
        "INSERT INTO pdata_meta (machine_id, revision, updated_at) VALUES (?, ?, ?)",
        [(machine_id, revision, updated_at) for machine_id, revision in vector.items()],
    )


def bump_own(conn: sqlite3.Connection, machine_id: str, *, updated_at: int | None = None) -> None:
    """Convenience used by every service.py write path: read, bump, write, all inside the
    caller's already-open transaction."""
    v = read_vector(conn)
    vector_clock.bump_own(v, machine_id)
    write_vector(conn, v, updated_at=updated_at if updated_at is not None else int(time.time()))
