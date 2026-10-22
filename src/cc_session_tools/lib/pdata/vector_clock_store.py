"""Reads/writes the pdata_meta table (spec: "The vector clock") and the pdata_migration_origin
table for one already-open connection. Pure vector math lives in vector_clock.py — this module is
the only place that touches SQL for either. Caller owns the transaction (wrap writes in
repository._immediate), matching every other write path in this package."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from typing import Final

from cc_session_tools.lib.pdata import vector_clock

# The format 3 vector clock a format 4 database was migrated from (design D6c): one row per
# machine, empty for a database created as format 4. It is ordinary table data, so it replicates
# with every dump and rehydrate, and dump.write_latest copies it into every dump header.
MIGRATION_ORIGIN_DDL: Final = """
CREATE TABLE IF NOT EXISTS pdata_migration_origin (
    machine_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL
);
"""


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


def read_migration_origin(conn: sqlite3.Connection) -> dict[str, int]:
    """The recorded format 3 origin vector, or {} when there is none. A database rehydrated
    from a dump published before the table existed has no such table until its next
    repository.connect(), and has no origin either."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pdata_migration_origin'"
    ).fetchone()
    if exists is None:
        return {}
    rows = conn.execute("SELECT machine_id, revision FROM pdata_migration_origin").fetchall()
    return {row[0]: row[1] for row in rows}


def write_migration_origin(conn: sqlite3.Connection, vector: Mapping[str, int]) -> None:
    """Replace the recorded origin vector with `vector`. Creates the table if needed: the
    migration writes it into a database converted from format 3, which never had it."""
    conn.execute(MIGRATION_ORIGIN_DDL)
    conn.execute("DELETE FROM pdata_migration_origin")
    conn.executemany(
        "INSERT INTO pdata_migration_origin (machine_id, revision) VALUES (?, ?)",
        sorted(vector.items()),
    )
