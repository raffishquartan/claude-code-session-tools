"""Builds a format 3 (integer record id) pdata database the way ccst 3.x created one, for tests
that need an unmigrated project. Written with plain sqlite3 and the 3.x DDL verbatim so the
fixture does not depend on any 4.x code path."""
from __future__ import annotations

import hashlib
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


def write_format3_dump(
    db_path: Path, project_root: Path, *, machine_id: str, vector: dict[str, int],
    dumped_at: int = 1700000000,
) -> Path:
    """Publish `db_path` to `project_root/.pdata-db-dump/latest.sql` the way ccst 3.x did: no
    `-- pdata_format=` line, the machine_id/dumped_at/vector header, then the serialised body,
    with `latest.sha256` beside it. The body uses `dump.serialize`, whose output 3.x shared.
    Returns the dump directory."""
    from cc_session_tools.lib.pdata import dump

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        body = dump.serialize(conn)
    finally:
        conn.close()
    header = f"-- machine_id={machine_id}\n-- dumped_at={dumped_at}\n"
    header += "\n".join(f"-- vector:{k}={v}" for k, v in sorted(vector.items())) + "\n"
    text = header + body
    dump_dir = project_root / ".pdata-db-dump"
    dump_dir.mkdir(parents=True, exist_ok=True)
    (dump_dir / "latest.sql").write_text(text)
    (dump_dir / "latest.sha256").write_text(hashlib.sha256(text.encode()).hexdigest())
    return dump_dir
