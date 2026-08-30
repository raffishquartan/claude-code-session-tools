"""Deterministic text serialisation of one project's .db (spec: "Dump format"). NOT
sqlite3.Connection.iterdump() used as-is — empirically confirmed during design that a table with
a composite/non-integer primary key (record_group_fields) dumps in insertion order under
iterdump(), not key order, making two logically-identical databases with different edit histories
produce different dump bytes. This module sorts tables/indices by name explicitly and adds an
explicit ORDER BY per table's own primary-key columns, rather than relying on iterdump()'s
unordered per-table fetch."""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_ARCHIVE_KEEP = 24


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    pk_rows = [r for r in rows if r["pk"] > 0]
    pk_rows.sort(key=lambda r: r["pk"])  # PRAGMA table_info's pk column is the PK's column *order*
    return [r["name"] for r in pk_rows]


def serialize(conn: sqlite3.Connection) -> str:
    lines = ["BEGIN TRANSACTION;"]
    schema_rows = conn.execute(
        "SELECT name, type, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND type IN ('table', 'index') AND name != 'sqlite_sequence' "
        "ORDER BY name"
    ).fetchall()
    for row in schema_rows:
        if row["type"] != "table":
            continue
        lines.append(row["sql"] + ";")
        pk_cols = _primary_key_columns(conn, row["name"])
        order_by = f" ORDER BY {', '.join(pk_cols)}" if pk_cols else ""
        data_rows = conn.execute(f'SELECT * FROM "{row["name"]}"{order_by}').fetchall()
        for data_row in data_rows:
            values = ", ".join(_sql_literal(v) for v in tuple(data_row))
            lines.append(f'INSERT INTO "{row["name"]}" VALUES({values});')
    for row in schema_rows:
        if row["type"] == "index":
            lines.append(row["sql"] + ";")
    lines.append("COMMIT;")
    return "\n".join(lines)


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


@dataclass(frozen=True, slots=True)
class DumpInfo:
    checksum_valid: bool
    machine_id: str | None
    vector: dict[str, int]


def _dump_dir(project_root: Path) -> Path:
    return project_root / ".pdata-db-dump"


def _unique_archive_path(archive_dir: Path, timestamp: int) -> Path:
    """A filename for this archived dump that does not already exist in archive_dir.

    write_latest can run more than once within the same wall-clock second — e.g. the
    archive-pruning test below, which loops 30 times with no sleep — so a bare `f"{timestamp}.sql"`
    is not unique by itself: shutil.copy2 onto a colliding name silently overwrites the earlier
    archived dump instead of keeping both, under-filling the archive regardless of how
    _prune_archive's retention count is set. Appending an incrementing suffix on collision keeps
    every write distinct regardless of the underlying clock's resolution."""
    candidate = archive_dir / f"{timestamp}.sql"
    suffix = 1
    while candidate.exists():
        candidate = archive_dir / f"{timestamp}-{suffix}.sql"
        suffix += 1
    return candidate


def write_latest(
    conn: sqlite3.Connection, *, project_root: Path, machine_id: str, vector: dict[str, int],
) -> None:
    text = serialize(conn)
    header = f"-- machine_id={machine_id}\n-- dumped_at={int(time.time())}\n"
    header += "\n".join(f"-- vector:{k}={v}" for k, v in sorted(vector.items())) + "\n"
    full_text = header + text
    checksum = hashlib.sha256(full_text.encode()).hexdigest()

    dump_dir = _dump_dir(project_root)
    archive_dir = dump_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    latest = dump_dir / "latest.sql"
    if latest.exists():
        shutil.copy2(latest, _unique_archive_path(archive_dir, int(time.time())))
        _prune_archive(archive_dir)

    latest.write_text(full_text)
    (dump_dir / "latest.sha256").write_text(checksum)


def _prune_archive(archive_dir: Path) -> None:
    files = sorted(archive_dir.glob("*.sql"))
    for stale in files[:-_ARCHIVE_KEEP] if len(files) > _ARCHIVE_KEEP else []:
        stale.unlink()


def read_latest(project_root: Path) -> DumpInfo:
    dump_dir = _dump_dir(project_root)
    latest = dump_dir / "latest.sql"
    checksum_file = dump_dir / "latest.sha256"
    if not latest.exists() or not checksum_file.exists():
        return DumpInfo(checksum_valid=False, machine_id=None, vector={})
    text = latest.read_text()
    actual = hashlib.sha256(text.encode()).hexdigest()
    expected = checksum_file.read_text().strip()
    if actual != expected:
        return DumpInfo(checksum_valid=False, machine_id=None, vector={})
    machine_id = None
    vector: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("-- machine_id="):
            machine_id = line.removeprefix("-- machine_id=")
        elif line.startswith("-- vector:"):
            rest = line.removeprefix("-- vector:")
            k, _, v = rest.partition("=")
            vector[k] = int(v)
    return DumpInfo(checksum_valid=True, machine_id=machine_id, vector=vector)
