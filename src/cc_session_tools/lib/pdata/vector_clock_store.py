"""Reads/writes the pdata_meta table (spec: "The vector clock") and the migration origin tables
(pdata_migration_origin, pdata_migrated_from) for one already-open connection. Pure vector math lives in vector_clock.py - this module is
the only place that touches SQL for either. Caller owns the transaction (wrap writes in
repository._immediate), matching every other write path in this package."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from cc_session_tools.lib import db
from cc_session_tools.lib.pdata import formats, vector_clock

# The migration origin of a format 4 database (design D6c). `pdata_migrated_from` holds one row,
# the format the database was migrated from (3), when it was converted by `ccst pdata
# migrate-uuid`, and none when it was created as format 4. `pdata_migration_origin` holds the
# format 3 vector clock it was migrated from, one row per machine; that vector may be empty, so
# it is the presence row, not the vector, that says the database was migrated. Both are ordinary
# table data, so they replicate with every dump and rehydrate, and dump.write_latest copies the
# origin into every dump header.
MIGRATION_ORIGIN_DDL: Final = """
CREATE TABLE IF NOT EXISTS pdata_migration_origin (
    machine_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pdata_migrated_from (
    pdata_format INTEGER PRIMARY KEY
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


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def read_migration_origin(conn: sqlite3.Connection) -> dict[str, int] | None:
    """The format 3 vector the database was migrated from (possibly empty), or None when it was
    not migrated. A recorded vector row counts as an origin with or without the presence row. A
    database loaded from a dump that lacks these tables has neither until repository.connect()
    creates them, and has no origin."""
    vector: dict[str, int] = {}
    if _table_exists(conn, "pdata_migration_origin"):
        rows = conn.execute("SELECT machine_id, revision FROM pdata_migration_origin").fetchall()
        vector = {row[0]: row[1] for row in rows}
    migrated = _table_exists(conn, "pdata_migrated_from") and conn.execute(
        "SELECT 1 FROM pdata_migrated_from WHERE pdata_format=?",
        (formats.PDATA_FORMAT_INTEGER_IDS,),
    ).fetchone() is not None
    return vector if migrated or vector else None


def migration_unfinished(path: Path) -> bool:
    """True when the database at `path` was converted by `ccst pdata migrate-uuid` (it has a
    migration origin) but its `pdata-uuid-ids` marker is not recorded: the run that converted it
    stopped before verifying it. Opens the file read-only."""
    conn = db.connect(path, readonly=True)
    try:
        if read_migration_origin(conn) is None:
            return False
        return not (
            _table_exists(conn, "migrations")
            and db.migration_applied(conn, formats.UUID_IDS_MIGRATION)
        )
    finally:
        conn.close()


def write_migration_origin(conn: sqlite3.Connection, vector: Mapping[str, int]) -> None:
    """Record that the database was migrated from format 3 at `vector` (which may be empty),
    replacing any recorded origin. Creates the tables if needed: the migration writes them into
    a database converted from format 3, which never had them."""
    # One statement per execute(): executescript() would commit the caller's transaction.
    for statement in MIGRATION_ORIGIN_DDL.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("DELETE FROM pdata_migration_origin")
    conn.executemany(
        "INSERT INTO pdata_migration_origin (machine_id, revision) VALUES (?, ?)",
        sorted(vector.items()),
    )
    conn.execute("DELETE FROM pdata_migrated_from")
    conn.execute(
        "INSERT INTO pdata_migrated_from (pdata_format) VALUES (?)",
        (formats.PDATA_FORMAT_INTEGER_IDS,),
    )
