"""sessions.db — consolidates the session-tag cache, per-session activity
sentinels (.last-opened / .last-active), and the doctor drift-mute store into
one SQLite file under paths.data_home().

Replaces three flat-file stores:
  - ~/.cache/claude/session-tags/<uuid>.tag           -> session_tags table
  - cc-sessions/<basename>/.last-opened, .last-active  -> sessions table
  - ~/.claude/cc-doctor-mutes.json                     -> doctor_mutes table
    (doctor_mutes.py stays the public-facing module for that table; it
    imports DDL/default_db_path/connect from here so all three tables share
    one schema and one file.)

Every read/write opens a connection via connect(), which delegates to the
Phase 1 shared helper cc_session_tools.lib.db.connect() (WAL mode, busy
timeout, dict-style rows). Connections are opened and closed per call,
matching the existing per-call pattern in cccs_hooks/cache.py — WAL mode is
specifically designed for many short-lived writers from different processes
(hooks fire once per SessionStart/Stop event), so this needs no pooling.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import db, paths

SESSIONS_DIR_ENV = "CCST_SESSIONS_DIR"
_DB_FILENAME = "sessions.db"

DDL = """
CREATE TABLE IF NOT EXISTS session_tags (
    uuid       TEXT PRIMARY KEY,
    tag        TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    project_dir   TEXT NOT NULL,
    basename      TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    last_opened   REAL,
    last_active   REAL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (project_dir, basename)
);
CREATE INDEX IF NOT EXISTS idx_sessions_basename    ON sessions(basename);
CREATE INDEX IF NOT EXISTS idx_sessions_start_date  ON sessions(start_date);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions(last_active);
CREATE INDEX IF NOT EXISTS idx_sessions_last_opened ON sessions(last_opened);

CREATE TABLE IF NOT EXISTS doctor_mutes (
    name     TEXT PRIMARY KEY,
    muted_at TEXT NOT NULL
);
"""


def default_db_path() -> Path:
    """sessions.db location. Overridable via CCST_SESSIONS_DIR (a directory);
    falls back to paths.data_home()."""
    override = os.environ.get(SESSIONS_DIR_ENV)
    base = Path(override) if override else paths.data_home()
    return base / _DB_FILENAME


def connect(*, path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    """Open sessions.db (or an explicit override path — used by tests and by
    ccst doctor --mutes-file). readonly=True skips schema creation; callers
    that only read must handle sqlite3.OperationalError for a not-yet-created
    file (see lookup_tags/list_sessions/find_exact for the established
    graceful-degradation pattern)."""
    target = path if path is not None else default_db_path()
    if readonly:
        return db.connect(target, readonly=True)
    return db.connect(target, ddl=DDL)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# session_tags
# ---------------------------------------------------------------------------

def write_tag(uuid: str, tag: str, *, path: Path | None = None) -> None:
    """Record (or update) the tag for a session uuid."""
    conn = connect(path=path)
    try:
        conn.execute(
            "INSERT INTO session_tags (uuid, tag, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(uuid) DO UPDATE SET tag=excluded.tag, updated_at=excluded.updated_at",
            (uuid, tag, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def lookup_tags(uuids: list[str], *, path: Path | None = None) -> dict[str, str]:
    """Batch uuid -> tag lookup. Returns {} for an empty input list (without
    opening a connection) and for a sessions.db that has never been written
    to (no writer has run yet — not an error condition for a reader)."""
    if not uuids:
        return {}
    try:
        conn = connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return {}
    try:
        placeholders = ",".join("?" for _ in uuids)
        rows = conn.execute(
            f"SELECT uuid, tag FROM session_tags WHERE uuid IN ({placeholders})",
            uuids,
        ).fetchall()
        return {r["uuid"]: r["tag"] for r in rows}
    finally:
        conn.close()
