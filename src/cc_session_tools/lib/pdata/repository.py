"""SQLite data-access layer for per-project data stores (spec §4).

The single home of all SQL for the base records/record_group_fields tables and every
ext_<record_group> extension table. Callers go through service.py for validation; this module
trusts its inputs are already validated (record_group/field-name charset, project name).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from cc_session_tools.lib import db
from cc_session_tools.lib.pdata import naming, store

_BASE_DDL = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    record_group TEXT NOT NULL,
    content TEXT NOT NULL,
    file_path TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    deleted_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_records_group ON records(record_group);
CREATE INDEX IF NOT EXISTS idx_records_updated ON records(updated_at);

CREATE TABLE IF NOT EXISTS record_group_fields (
    record_group TEXT NOT NULL,
    field_name TEXT NOT NULL,
    description TEXT,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (record_group, field_name)
);
"""


def connect(project: str) -> sqlite3.Connection:
    """Open <project>.db through the shared helper, in explicit-transaction mode.

    isolation_level=None turns off sqlite3's implicit BEGIN so callers issue their own
    BEGIN IMMEDIATE for multi-statement writes (see _immediate), matching
    lib/messaging/repository.py's connect()."""
    conn = db.connect(store.db_path(project), ddl=_BASE_DDL)
    conn.isolation_level = None
    return conn


@contextmanager
def _immediate(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the body inside a BEGIN IMMEDIATE / COMMIT, rolling back on error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def insert_base_record(
    conn: sqlite3.Connection,
    *,
    record_group: str,
    content: str,
    file_path: str | None,
    created_at: int,
    updated_at: int,
) -> int:
    """Insert one records row (caller already validated record_group). Returns the new id.
    Caller owns the transaction (wrap in _immediate if this isn't the only statement)."""
    cur = conn.execute(
        "INSERT INTO records (record_group, content, file_path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (record_group, content, file_path, created_at, updated_at),
    )
    assert cur.lastrowid is not None  # sqlite3 always sets this after a successful INSERT
    return cur.lastrowid


def get_base_record(conn: sqlite3.Connection, record_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
