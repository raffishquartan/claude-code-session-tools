"""Builds a format 3 (integer record id) pdata database the way ccst 3.x created one, for tests
that need an unmigrated project. Written with plain sqlite3 and the 3.x DDL verbatim so the
fixture does not depend on any 4.x code path."""
from __future__ import annotations

import sqlite3
from pathlib import Path

FORMAT3_DDL = """
CREATE TABLE records (
    id INTEGER PRIMARY KEY,
    record_group TEXT NOT NULL,
    content TEXT NOT NULL,
    file_path TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    deleted_at INTEGER
);
CREATE INDEX idx_records_group ON records(record_group);
CREATE INDEX idx_records_updated ON records(updated_at);
CREATE TABLE record_group_fields (
    record_group TEXT NOT NULL,
    field_name TEXT NOT NULL,
    description TEXT,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (record_group, field_name)
);
CREATE TABLE pdata_meta (
    machine_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE "ext_notes" (record_id INTEGER PRIMARY KEY REFERENCES records(id), topic TEXT);
"""


def make_format3_db(path: Path) -> Path:
    """Create `path` as a format 3 database holding two `notes` records (one soft-deleted) and a
    vector clock row. Returns `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(FORMAT3_DDL)
        conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (1, 'notes', 'first', NULL, 1000, 1000)"
        )
        conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at, "
            "deleted_at) VALUES (2, 'notes', 'second', NULL, 1001, 1001, 1002)"
        )
        conn.execute("INSERT INTO ext_notes (record_id, topic) VALUES (1, 'a'), (2, 'b')")
        conn.execute(
            "INSERT INTO pdata_meta (machine_id, revision, updated_at) "
            "VALUES ('machine-a', 3, 1002)"
        )
        conn.commit()
    finally:
        conn.close()
    return path


def snapshot_dir(directory: Path) -> dict[str, bytes]:
    """Every file under `directory` and its bytes, for asserting a command wrote nothing."""
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.rglob("*")) if p.is_file()
    }
