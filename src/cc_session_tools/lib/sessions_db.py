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
matching the existing per-call pattern in hooks/cache.py — WAL mode is
specifically designed for many short-lived writers from different processes
(hooks fire once per SessionStart/Stop event), so this needs no pooling.
"""
from __future__ import annotations

import os
import sqlite3
import sys
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
    updated_at TEXT NOT NULL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    project_dir   TEXT NOT NULL,
    basename      TEXT NOT NULL,
    uuid          TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    last_opened   REAL,
    last_active   REAL,
    discovered_at TEXT NOT NULL,
    updated_at    TEXT,
    PRIMARY KEY (project_dir, basename, uuid)
);
CREATE INDEX IF NOT EXISTS idx_sessions_basename    ON sessions(basename);
CREATE INDEX IF NOT EXISTS idx_sessions_start_date  ON sessions(start_date);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions(last_active);
CREATE INDEX IF NOT EXISTS idx_sessions_last_opened ON sessions(last_opened);
-- fast "how many uuids share this (project_dir, basename)" lookup for fork detection
CREATE INDEX IF NOT EXISTS idx_sessions_proj_basename ON sessions(project_dir, basename);

-- Rows the 3.0.0 uuid migration could not confidently assign a uuid to (zero matching
-- transcripts found - see migrate_sessions_db.migrate_uuid()). Never copied into `sessions`
-- itself: a placeholder uuid value there would be a legal PK tuple that becomes a permanent
-- phantom fork the next time the session is genuinely opened (see design.md Decision 7 in
-- openspec/changes/release-3-0-0/). Reconciled automatically by ensure_session_row/
-- touch_last_opened/touch_last_active the next time this (project_dir, basename) is written
-- with a real uuid, or explicitly via `ccst repair sessions --uuid`.
CREATE TABLE IF NOT EXISTS sessions_migration_ambiguous (
    project_dir   TEXT NOT NULL,
    basename      TEXT NOT NULL,
    reason        TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    flagged_at    TEXT NOT NULL,
    PRIMARY KEY (project_dir, basename)
);

CREATE TABLE IF NOT EXISTS doctor_mutes (
    name       TEXT PRIMARY KEY,
    muted_at   TEXT NOT NULL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS install_sync (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS context_overrides (
    session_id TEXT PRIMARY KEY,
    state      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_at TEXT
);
""" + db.MIGRATIONS_DDL

# Marker name recorded in the migrations table by cli.migrate_sessions_db once the legacy flat
# tag cache + activity sentinels + doctor-mutes JSON have been imported. Read by that script (to
# refuse a second run) and by lib.doctor (to tell "not yet imported" from "imported, old files
# not cleaned up").
LEGACY_FLAT_FILE_MIGRATION = "flat-files-to-sessions-db"

# Marker recorded once the `sessions` table's primary key has been rebuilt to
# (project_dir, basename, uuid) - either by a completed `ccst sessions migrate-uuid --write` run,
# or immediately at DDL-creation time for a brand-new file (see connect() below), which already
# has the 3-column PK and therefore has nothing to migrate. Read by lib.doctor
# (migration-to-3.0.0:sessions-uuid check) together with sessions_schema_is_uuid_keyed() - see
# design.md Decision 1-2 in openspec/changes/release-3-0-0/ for the full four-state truth table.
SESSIONS_UUID_MIGRATION = "sessions-uuid-pk"

# Columns added after these tables already shipped - CREATE TABLE IF NOT EXISTS above is a
# no-op against an existing table, so an already-initialised sessions.db needs these backfilled.
_BACKFILL_COLUMNS: dict[str, dict[str, str]] = {
    "session_tags": {"created_at": "TEXT"},
    "sessions": {"updated_at": "TEXT"},
    "doctor_mutes": {"created_at": "TEXT"},
    "install_sync": {"created_at": "TEXT"},
    "context_overrides": {"created_at": "TEXT"},
}


def default_db_path() -> Path:
    """sessions.db location. Overridable via CCST_SESSIONS_DIR (a directory);
    falls back to paths.data_home()."""
    override = os.environ.get(SESSIONS_DIR_ENV)
    base = Path(override) if override else paths.data_home()
    return base / _DB_FILENAME


def _migrate_tables(conn: sqlite3.Connection) -> None:
    for table, columns in _BACKFILL_COLUMNS.items():
        db.add_missing_columns(conn, table, columns)


def sessions_schema_is_uuid_keyed(conn: sqlite3.Connection) -> bool:
    """True if the `sessions` table's PRIMARY KEY is the 3-column
    (project_dir, basename, uuid) form, false for the pre-3.0.0 2-column form (or a
    `uuid` column that exists but was added via ALTER TABLE ADD COLUMN and is therefore
    NOT part of the key).

    Reads PRAGMA table_info's `pk` ordinal column (1-based position within the primary
    key, 0 = not in the key at all) rather than just checking "does a uuid column
    exist" - a bare ALTER TABLE ADD COLUMN uuid ... satisfies the latter while leaving
    the original fork bug (upserts still conflict on the old 2-column key) fully
    present. See design.md Decision 1-2."""
    cols = {row["name"]: row["pk"] for row in conn.execute("PRAGMA table_info(sessions)")}
    return (
        cols.get("project_dir") == 1
        and cols.get("basename") == 2
        and cols.get("uuid") == 3
    )


def connect(*, path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    """Open sessions.db (or an explicit override path — used by tests and by
    ccst doctor --mutes-file). readonly=True skips schema creation; callers
    that only read must handle sqlite3.OperationalError for a not-yet-created
    file (see lookup_tags/list_sessions/find_exact for the established
    graceful-degradation pattern).

    A brand-new file (did not exist before this call) has the current DDL's 3-column
    PK from the moment it is created, so it records SESSIONS_UUID_MIGRATION immediately
    - it has nothing to migrate, and must never be reported as pending by `ccst doctor`
    (see design.md Decision 1-2's fresh-install handling). An existing pre-3.0.0 file is
    untouched here: CREATE TABLE IF NOT EXISTS is a no-op against its old 2-column-PK
    table, and only `ccst sessions migrate-uuid --write` rebuilds it."""
    target = path if path is not None else default_db_path()
    if readonly:
        return db.connect(target, readonly=True)
    is_new_file = not target.exists()
    conn = db.connect(target, ddl=DDL)
    _migrate_tables(conn)
    if is_new_file:
        db.record_migration(conn, SESSIONS_UUID_MIGRATION, applied_at=_now_iso())
        conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# session_tags
# ---------------------------------------------------------------------------

def write_tag(
    uuid: str, tag: str, *, path: Path | None = None, conn: sqlite3.Connection | None = None
) -> None:
    """Record (or update) the tag for a session uuid.

    conn, if given, is reused as-is (caller owns commit/close) instead of opening
    a fresh connection — lets a bulk caller like migrate_sessions_db.py write
    thousands of rows in one connection/transaction instead of one connect() +
    DDL re-run + commit + close per row.
    """
    owns_conn = conn is None
    c = conn if conn is not None else connect(path=path)
    try:
        now_iso = _now_iso()
        c.execute(
            "INSERT INTO session_tags (uuid, tag, updated_at, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(uuid) DO UPDATE SET tag=excluded.tag, updated_at=excluded.updated_at",
            (uuid, tag, now_iso, now_iso),
        )
        if owns_conn:
            c.commit()
    finally:
        if owns_conn:
            c.close()


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
    uuid: str
    start_date: str
    last_opened: float
    last_active: float


def _row_to_session(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        project_dir=Path(row["project_dir"]),
        basename=row["basename"],
        uuid=row["uuid"],
        start_date=row["start_date"],
        last_opened=row["last_opened"] or 0.0,
        last_active=row["last_active"] or 0.0,
    )


@dataclass(frozen=True, slots=True)
class AmbiguousRow:
    """A `sessions` row the uuid migration could not confidently resolve - see
    sessions_migration_ambiguous in the DDL and design.md Decision 7."""
    project_dir: Path
    basename: str
    reason: str
    start_date: str
    discovered_at: str
    flagged_at: str


def _row_to_ambiguous(row: sqlite3.Row) -> AmbiguousRow:
    return AmbiguousRow(
        project_dir=Path(row["project_dir"]),
        basename=row["basename"],
        reason=row["reason"],
        start_date=row["start_date"],
        discovered_at=row["discovered_at"],
        flagged_at=row["flagged_at"],
    )


def _reject_non_absolute_project_dir(project_dir: Path, basename: str, *, source: str) -> None:
    """Print a clear, unmissable diagnostic to stderr — visible to a human reading the
    terminal AND to Claude Code, which surfaces hook stderr as tool/hook output. A silent
    rejection here means this row is permanently invisible to `ccl`/`ccs --global` until
    repaired, so every rejection must say so out loud."""
    print(
        f"[sessions-db] REJECTED write from {source}: project_dir={str(project_dir)!r} for "
        f"session {basename!r} is not absolute. This session will be MISSING from `ccl`/"
        "`ccs --global` listings until fixed — run 'ccst repair sessions --dry-run' to see "
        "it, then --execute to fix.",
        file=sys.stderr,
    )


def _is_valid_project_dir(project_dir: Path, basename: str, *, source: str) -> bool:
    if project_dir.is_absolute():
        return True
    _reject_non_absolute_project_dir(project_dir, basename, source=source)
    return False


def _reconcile_ambiguous_row(c: sqlite3.Connection, project_dir: Path, basename: str) -> None:
    """Delete any sessions_migration_ambiguous row for (project_dir, basename) - called
    before every real write to `sessions`. A row only ever lands in the sidecar because
    the 3.0.0 migration found no matching transcript for it at migration time (see
    design.md Decision 7); the moment this basename is genuinely written again with a
    real uuid, the sidecar entry is stale and must go, or it would sit there forever
    describing a session that has since been discovered. Cheap no-op when the sidecar
    table is empty, which is the common case."""
    c.execute(
        "DELETE FROM sessions_migration_ambiguous WHERE project_dir = ? AND basename = ?",
        (str(project_dir), basename),
    )


def ensure_session_row(
    project_dir: Path,
    basename: str,
    *,
    uuid: str,
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Insert a row for (project_dir, basename, uuid) if absent. Never overwrites an
    existing row's timestamps — this is the safety-net call ccd.py makes right
    after creating a session directory, in case the SessionStart hook never
    fires (hooks disabled/broken); the hook's own touch_last_opened() upsert
    is the normal path and would create the same row moments later regardless.

    Also a no-op — with a loud stderr diagnostic — when `project_dir` is not
    absolute; see `_is_valid_project_dir`.

    conn, if given, is reused as-is (caller owns commit/close) — see write_tag."""
    from cc_session_tools.lib.sessions import session_start_date

    start_date = session_start_date(basename)
    if start_date is None:
        return
    if not _is_valid_project_dir(project_dir, basename, source="ensure_session_row"):
        return
    owns_conn = conn is None
    c = conn if conn is not None else connect(path=path)
    try:
        _reconcile_ambiguous_row(c, project_dir, basename)
        c.execute(
            "INSERT INTO sessions (project_dir, basename, uuid, start_date, discovered_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename, uuid) DO NOTHING",
            (str(project_dir), basename, uuid, start_date, _now_iso()),
        )
        if owns_conn:
            c.commit()
    finally:
        if owns_conn:
            c.close()


def touch_last_opened(
    project_dir: Path,
    basename: str,
    *,
    uuid: str,
    path: Path | None = None,
    when: float | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Upsert the last_opened timestamp (epoch seconds) for (project_dir, basename, uuid).
    Two forks of the same (project_dir, basename) sharing different uuids land in two
    distinct rows instead of clobbering each other's timestamps - the fix this schema
    change exists for.

    Also a no-op — with a loud stderr diagnostic — when `project_dir` is not
    absolute; see `_is_valid_project_dir`.

    conn, if given, is reused as-is (caller owns commit/close) — see write_tag."""
    from cc_session_tools.lib.sessions import session_start_date

    start_date = session_start_date(basename)
    if start_date is None:
        return
    if not _is_valid_project_dir(project_dir, basename, source="touch_last_opened"):
        return
    ts = when if when is not None else time.time()
    owns_conn = conn is None
    c = conn if conn is not None else connect(path=path)
    try:
        _reconcile_ambiguous_row(c, project_dir, basename)
        c.execute(
            "INSERT INTO sessions "
            "(project_dir, basename, uuid, start_date, discovered_at, last_opened, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename, uuid) DO UPDATE SET "
            "last_opened=excluded.last_opened, updated_at=excluded.updated_at",
            (str(project_dir), basename, uuid, start_date, _now_iso(), ts, _now_iso()),
        )
        if owns_conn:
            c.commit()
    finally:
        if owns_conn:
            c.close()


def touch_last_active(
    project_dir: Path,
    basename: str,
    *,
    uuid: str,
    path: Path | None = None,
    when: float | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Upsert the last_active timestamp (epoch seconds) for (project_dir, basename, uuid).
    Two forks of the same (project_dir, basename) sharing different uuids land in two
    distinct rows instead of clobbering each other's timestamps - the fix this schema
    change exists for.

    Also a no-op — with a loud stderr diagnostic — when `project_dir` is not
    absolute; see `_is_valid_project_dir`.

    conn, if given, is reused as-is (caller owns commit/close) — see write_tag."""
    from cc_session_tools.lib.sessions import session_start_date

    start_date = session_start_date(basename)
    if start_date is None:
        return
    if not _is_valid_project_dir(project_dir, basename, source="touch_last_active"):
        return
    ts = when if when is not None else time.time()
    owns_conn = conn is None
    c = conn if conn is not None else connect(path=path)
    try:
        _reconcile_ambiguous_row(c, project_dir, basename)
        c.execute(
            "INSERT INTO sessions "
            "(project_dir, basename, uuid, start_date, discovered_at, last_active, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename, uuid) DO UPDATE SET "
            "last_active=excluded.last_active, updated_at=excluded.updated_at",
            (str(project_dir), basename, uuid, start_date, _now_iso(), ts, _now_iso()),
        )
        if owns_conn:
            c.commit()
    finally:
        if owns_conn:
            c.close()


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
        query = (
            "SELECT project_dir, basename, uuid, start_date, last_opened, last_active "
            "FROM sessions"
        )
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
    """Remove every sessions-table row for (project_dir, basename) - i.e. every fork
    of this tag, not just one, since (project_dir, basename) is no longer the full key
    (uuid is) but all of a tag's forks share one on-disk cc-sessions/<basename>/
    directory being deleted together; leaving orphan rows for the other forks would be
    worse than removing them all. Returns True if at least one row was deleted. Used by
    the delete-sessions skill so a deleted session stops appearing in ccs/ccr
    enumeration (there is no automatic GC — see D6)."""
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


def delete_session_row_exact(
    project_dir: Path, basename: str, uuid: str, *, path: Path | None = None
) -> bool:
    """Remove exactly the one (project_dir, basename, uuid) row - unlike
    delete_session_row(), which deletes every fork sharing (project_dir, basename)
    (correct for "delete this whole session directory", wrong for a caller like
    move_session.py that re-keys one specific fork's row at a time and must not touch
    its siblings). Returns True if a row was deleted."""
    conn = connect(path=path)
    try:
        cur = conn.execute(
            "DELETE FROM sessions WHERE project_dir = ? AND basename = ? AND uuid = ?",
            (str(project_dir), basename, uuid),
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
            "SELECT project_dir, basename, uuid, start_date, last_opened, last_active "
            "FROM sessions WHERE basename = ?",
            (basename,),
        ).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# sessions_migration_ambiguous
# ---------------------------------------------------------------------------

def record_ambiguous_row(
    project_dir: Path,
    basename: str,
    reason: str,
    *,
    start_date: str,
    discovered_at: str,
    path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Flag a (project_dir, basename) the uuid migration could not resolve. Called only
    by migrate_sessions_db.migrate_uuid() during the rebuild - conn, if given, is the
    same connection/transaction the rebuild itself runs in (caller owns commit/close),
    matching the write_tag()/ensure_session_row() convention."""
    owns_conn = conn is None
    c = conn if conn is not None else connect(path=path)
    try:
        c.execute(
            "INSERT INTO sessions_migration_ambiguous "
            "(project_dir, basename, reason, start_date, discovered_at, flagged_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename) DO UPDATE SET "
            "reason=excluded.reason, flagged_at=excluded.flagged_at",
            (str(project_dir), basename, reason, start_date, discovered_at, _now_iso()),
        )
        if owns_conn:
            c.commit()
    finally:
        if owns_conn:
            c.close()


def list_ambiguous_rows(*, path: Path | None = None) -> list[AmbiguousRow]:
    """Every row still sitting in the migration-ambiguous sidecar, for `ccst repair
    sessions --uuid` to inspect. Empty list if sessions.db has never been written to."""
    try:
        conn = connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT project_dir, basename, reason, start_date, discovered_at, flagged_at "
            "FROM sessions_migration_ambiguous"
        ).fetchall()
        return [_row_to_ambiguous(r) for r in rows]
    finally:
        conn.close()


def resolve_ambiguous_row(
    project_dir: Path, basename: str, uuid: str, *, path: Path | None = None
) -> bool:
    """Manually assign a uuid to a sidecar row: inserts it into `sessions` using its
    own stored start_date/discovered_at, then removes it from the sidecar. Returns
    False (no-op) if no matching sidecar row exists. Used by `ccst repair sessions
    --uuid` for a row an operator has independently identified the correct uuid for."""
    conn = connect(path=path)
    try:
        row = conn.execute(
            "SELECT start_date, discovered_at FROM sessions_migration_ambiguous "
            "WHERE project_dir = ? AND basename = ?",
            (str(project_dir), basename),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            "INSERT INTO sessions (project_dir, basename, uuid, start_date, discovered_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename, uuid) DO NOTHING",
            (str(project_dir), basename, uuid, row["start_date"], row["discovered_at"]),
        )
        conn.execute(
            "DELETE FROM sessions_migration_ambiguous WHERE project_dir = ? AND basename = ?",
            (str(project_dir), basename),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_ambiguous_row(project_dir: Path, basename: str, *, path: Path | None = None) -> bool:
    """Discard a sidecar row outright (e.g. its transcript is confirmed gone for good).
    Returns True if a row was deleted."""
    conn = connect(path=path)
    try:
        cur = conn.execute(
            "DELETE FROM sessions_migration_ambiguous WHERE project_dir = ? AND basename = ?",
            (str(project_dir), basename),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
