# src/cc_session_tools/lib/claude_md_store.py
"""sessions.db storage for the managed CLAUDE.md blocks: the hash of the text ccst last
wrote per (file, section), and backups of blocks that were overwritten after a hand edit.

The hash is what lets ``claude_md_install`` tell a hand-edited block from one that is merely
out of date with the template."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib import sessions_db


@dataclass(frozen=True)
class BlockBackup:
    id: int
    taken_at: str
    path: str
    section: str
    text: str


def block_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def block_sha256(block_text: str) -> str:
    """Hash of a block's text, ignoring trailing newlines so a block read back from a file
    compares equal to the rendered template."""
    return hashlib.sha256(block_text.rstrip("\n").encode("utf-8")).hexdigest()


def read_recorded_hashes(path: Path, *, db_path: Path | None = None) -> dict[str, str]:
    """Section id -> sha256 ccst last wrote into ``path``. Empty when nothing is recorded,
    including when sessions.db or the table does not exist yet (this is a read, so it must
    not create the store)."""
    try:
        conn = sessions_db.connect(path=db_path, readonly=True)
    except sqlite3.OperationalError:
        return {}
    try:
        rows = conn.execute(
            "SELECT section, sha256 FROM claude_md_blocks WHERE path = ?", (block_key(path),)
        ).fetchall()
        return {row["section"]: row["sha256"] for row in rows}
    except sqlite3.DatabaseError:
        return {}  # "no such table" on a pre-upgrade db, or a corrupt file
    finally:
        conn.close()


def record_hashes(
    conn: sqlite3.Connection, path: Path, hashes: dict[str, str],
) -> None:
    now = sessions_db._now_iso()
    for section, sha in hashes.items():
        conn.execute(
            "INSERT INTO claude_md_blocks (path, section, sha256, updated_at, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path, section) DO UPDATE SET "
            "sha256=excluded.sha256, updated_at=excluded.updated_at",
            (block_key(path), section, sha, now, now),
        )


def delete_hashes(conn: sqlite3.Connection, path: Path, sections: list[str]) -> None:
    for section in sections:
        conn.execute(
            "DELETE FROM claude_md_blocks WHERE path = ? AND section = ?",
            (block_key(path), section),
        )


def add_backup(conn: sqlite3.Connection, path: Path, section: str, text: str) -> int:
    now = sessions_db._now_iso()
    cursor = conn.execute(
        "INSERT INTO claude_md_backups (taken_at, path, section, text, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (now, block_key(path), section, text, now),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("sqlite did not return a row id for the inserted backup")
    return cursor.lastrowid


def list_backups(*, db_path: Path | None = None) -> list[BlockBackup]:
    try:
        conn = sessions_db.connect(path=db_path, readonly=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT id, taken_at, path, section, text FROM claude_md_backups ORDER BY id"
        ).fetchall()
        return [BlockBackup(r["id"], r["taken_at"], r["path"], r["section"], r["text"]) for r in rows]
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()


def get_backup(backup_id: int, *, db_path: Path | None = None) -> BlockBackup | None:
    for backup in list_backups(db_path=db_path):
        if backup.id == backup_id:
            return backup
    return None
