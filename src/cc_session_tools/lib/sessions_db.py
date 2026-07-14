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


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SessionRow:
    project_dir: Path
    basename: str
    start_date: str
    last_opened: float
    last_active: float


def _row_to_session(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        project_dir=Path(row["project_dir"]),
        basename=row["basename"],
        start_date=row["start_date"],
        last_opened=row["last_opened"] or 0.0,
        last_active=row["last_active"] or 0.0,
    )


def ensure_session_row(project_dir: Path, basename: str, *, path: Path | None = None) -> None:
    """Insert a row for (project_dir, basename) if absent. Never overwrites an
    existing row's timestamps — this is the safety-net call ccd.py makes right
    after creating a session directory, in case the SessionStart hook never
    fires (hooks disabled/broken); the hook's own touch_last_opened() upsert
    is the normal path and would create the same row moments later regardless."""
    from cc_session_tools.lib.sessions import session_start_date

    start_date = session_start_date(basename)
    if start_date is None:
        return
    conn = connect(path=path)
    try:
        conn.execute(
            "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename) DO NOTHING",
            (str(project_dir), basename, start_date, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def touch_last_opened(
    project_dir: Path, basename: str, *, path: Path | None = None, when: float | None = None
) -> None:
    """Upsert the last_opened timestamp (epoch seconds) for (project_dir, basename)."""
    from cc_session_tools.lib.sessions import session_start_date

    start_date = session_start_date(basename)
    if start_date is None:
        return
    ts = when if when is not None else time.time()
    conn = connect(path=path)
    try:
        conn.execute(
            "INSERT INTO sessions (project_dir, basename, start_date, discovered_at, last_opened) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename) DO UPDATE SET last_opened=excluded.last_opened",
            (str(project_dir), basename, start_date, _now_iso(), ts),
        )
        conn.commit()
    finally:
        conn.close()


def touch_last_active(
    project_dir: Path, basename: str, *, path: Path | None = None, when: float | None = None
) -> None:
    """Upsert the last_active timestamp (epoch seconds) for (project_dir, basename)."""
    from cc_session_tools.lib.sessions import session_start_date

    start_date = session_start_date(basename)
    if start_date is None:
        return
    ts = when if when is not None else time.time()
    conn = connect(path=path)
    try:
        conn.execute(
            "INSERT INTO sessions (project_dir, basename, start_date, discovered_at, last_active) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename) DO UPDATE SET last_active=excluded.last_active",
            (str(project_dir), basename, start_date, _now_iso(), ts),
        )
        conn.commit()
    finally:
        conn.close()


_ORDERABLE_COLUMNS = {"last_active", "last_opened"}


def list_sessions(
    *,
    project_dir: Path | None = None,
    path: Path | None = None,
    order_by: str | None = None,
    limit: int | None = None,
) -> list[SessionRow]:
    """Known sessions, optionally scoped to one project_dir.

    order_by/limit ("most recent N") push an indexed `ORDER BY <col> DESC
    LIMIT ?` into SQL when order_by is a DB-backed column (last_active /
    last_opened, both indexed - see idx_sessions_last_active/last_opened in
    the schema) - this is what makes "most recent N sessions" an O(log n)
    indexed lookup instead of fetching every row and slicing in Python.
    order_by values that need filesystem/Python-side computation (start,
    update - see ccs.py's --order-by) are NOT DB columns; callers needing
    those must pass order_by=None here and sort+slice the full result
    themselves (this is the documented, accepted exception - see D1 and the
    2026-07-13 performance requirement's explicit scoping in
    data-stores-design-spec.md Section 7.2, which only binds the title/tag-
    lookup path, not update-order's mtime walk).

    Empty list if sessions.db has never been written to, or if limit is
    given but no rows match.
    """
    if order_by is not None and order_by not in _ORDERABLE_COLUMNS:
        raise ValueError(f"order_by must be one of {_ORDERABLE_COLUMNS} or None, got {order_by!r}")
    if limit is not None and order_by is None:
        raise ValueError("limit requires order_by (an unordered LIMIT is meaningless)")

    try:
        conn = connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return []
    try:
        query = "SELECT project_dir, basename, start_date, last_opened, last_active FROM sessions"
        params: list[object] = []
        if project_dir is not None:
            query += " WHERE project_dir = ?"
            params.append(str(project_dir))
        if order_by is not None:
            # order_by is validated against _ORDERABLE_COLUMNS above (not
            # user-controlled free text) before this f-string runs, so this
            # is not a SQL-injection risk despite the interpolation.
            query += f" ORDER BY {order_by} DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()


def delete_session_row(project_dir: Path, basename: str, *, path: Path | None = None) -> bool:
    """Remove the sessions-table row for (project_dir, basename). Returns True
    if a row was deleted. Used by the delete-sessions skill so a deleted
    session stops appearing in ccs/ccr enumeration (there is no automatic GC —
    see D6)."""
    conn = connect(path=path)
    try:
        cur = conn.execute(
            "DELETE FROM sessions WHERE project_dir = ? AND basename = ?",
            (str(project_dir), basename),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_tag(uuid: str, *, path: Path | None = None) -> bool:
    """Remove the session_tags row for a uuid. Returns True if a row was
    deleted. Used by the delete-sessions skill to drop the tag mapping for a
    deleted session's transcript."""
    conn = connect(path=path)
    try:
        cur = conn.execute("DELETE FROM session_tags WHERE uuid = ?", (uuid,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def find_exact(basename: str, *, path: Path | None = None) -> list[SessionRow]:
    """Every row whose basename equals `basename` exactly (could be >1 if the
    same basename was created under two different project_dirs)."""
    try:
        conn = connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT project_dir, basename, start_date, last_opened, last_active "
            "FROM sessions WHERE basename = ?",
            (basename,),
        ).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()
