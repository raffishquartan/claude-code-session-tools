"""Shared SQLite connection-setup helper for every cc_session_tools .db store.

Every .db file under paths.data_home() opens through connect() so WAL mode
and an explicit busy-timeout are applied consistently, rather than each
subsystem module repeating pragma setup ad hoc. This is what prevents a
repeat of the exact drift that left statusline-usage.db (a different repo)
without WAL mode while its sibling command-cache.db got it right — see
data-stores-design-spec.md Section 7.3.
"""

from __future__ import annotations

import sqlite3
import time
import urllib.parse
from pathlib import Path

_BUSY_TIMEOUT_MS = 5000
_MIN_SQLITE_VERSION = (3, 35, 0)
_WAL_SWITCH_RETRY_SLEEP_S = 0.02

# Append to any store's ddl to give it a one-shot-migration ledger.
#
# Row count is not a usable "has the migration run?" signal for any store the
# running code also writes to: the new code starts writing the moment it is
# installed, so a non-empty table means "somebody wrote something", not
# "the legacy data was imported". A store that guesses from row counts either
# refuses to migrate data it has not migrated, or reports a migration as done
# that never happened. An explicit marker distinguishes the two.
MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


def _enable_wal(conn: sqlite3.Connection, *, deadline: float) -> None:
    """Switch a connection into WAL journal mode, retrying on lock.

    SQLite does NOT invoke the busy handler for a journal-mode change: if
    another connection holds a write lock at the moment WAL is enabled it
    returns SQLITE_BUSY ("database is locked") immediately, ignoring the
    busy_timeout that protects every other statement. Under cold-start
    concurrency (N processes/threads all creating the same fresh .db at once —
    exactly what a burst of hook fires does) this drops connections. Retry the
    switch ourselves within the same busy-timeout budget so the WAL switch is
    as patient as ordinary writes.
    """
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(_WAL_SWITCH_RETRY_SLEEP_S)


def connect(path: Path, *, ddl: str | None = None, readonly: bool = False) -> sqlite3.Connection:
    """Open path with WAL mode, an explicit busy-timeout, and dict-style rows.

    ddl, if given, is a CREATE TABLE/INDEX/VIEW IF NOT EXISTS multi-statement
    string executed (and committed) once per call — safe to pass on every
    connect(), including against an already-initialised file.

    readonly opens the file via a file: URI in mode=ro; ddl is ignored (and
    must be None) in that mode since a read-only handle cannot create schema.
    """
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
        raise RuntimeError(
            f"sqlite3 {sqlite3.sqlite_version} is too old (need >= "
            f"{'.'.join(map(str, _MIN_SQLITE_VERSION))}) for "
            "CREATE ... IF NOT EXISTS support"
        )

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    if readonly:
        if ddl is not None:
            raise ValueError("ddl is not supported with readonly=True")
        encoded = urllib.parse.quote(str(path), safe="/:")
        uri = f"file:{encoded}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=_BUSY_TIMEOUT_MS / 1000, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_MS / 1000, check_same_thread=False)
        try:
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            _enable_wal(conn, deadline=time.monotonic() + _BUSY_TIMEOUT_MS / 1000)
            conn.execute("PRAGMA foreign_keys=ON")
            if ddl:
                conn.executescript(ddl)
                conn.commit()
        except BaseException:
            conn.close()
            raise

    conn.row_factory = sqlite3.Row
    return conn


def migration_applied(conn: sqlite3.Connection, name: str) -> bool:
    """True if the named one-shot migration has been recorded on this store.

    Requires MIGRATIONS_DDL to be part of the store's schema.
    """
    row = conn.execute("SELECT 1 FROM migrations WHERE name = ?", (name,)).fetchone()
    return row is not None


def record_migration(conn: sqlite3.Connection, name: str, *, applied_at: str) -> None:
    """Mark the named one-shot migration as applied. Caller commits."""
    conn.execute(
        "INSERT OR REPLACE INTO migrations (name, applied_at) VALUES (?, ?)",
        (name, applied_at),
    )


def cas_update(
    conn: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    id_value: object,
    version_column: str,
    expected_version: int,
    set_clause: str,
    params: tuple[object, ...],
) -> bool:
    """Conditionally UPDATE one row, guarded by the version it was last read at.

    Returns True iff exactly one row changed - the caller supplied the version it most
    recently read, and no other writer has touched the row since. Returns False (zero rows
    changed) both when the row does not exist and when another writer already advanced its
    version - the caller must re-fetch to tell the two apart, this function does not.

    set_clause is caller-supplied SQL text (not a column->value dict) so each call site
    controls its own version-column increment (typically "<version_column> = <version_column>
    + 1") and any partial-update semantics (e.g. COALESCE(?, existing_column)) - this stays a
    thin, generic primitive extracted from lib/pdata/repository.py's proven pattern, not an ORM.
    Caller commits.
    """
    cur = conn.execute(
        f"UPDATE {table} SET {set_clause} WHERE {id_column}=? AND {version_column}=?",
        (*params, id_value, expected_version),
    )
    return cur.rowcount == 1


def checkpoint(conn: sqlite3.Connection) -> None:
    """Force a WAL checkpoint. Call before any filesystem-level copy of a live .db file."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def backup_to(source_path: Path, dest_path: Path) -> None:
    """Safely copy a live WAL-mode .db file using SQLite's own backup API.

    Safe against concurrent writers on source_path — no manual checkpoint or
    cp needed (sqlite3.Connection.backup() handles this internally). Used by
    `ccst backup run` (Phase 7) and by migration scripts' pre-cutover safety
    copies.

    The source opens through this module's own connect(readonly=True) — the same
    WAL/busy-timeout-aware helper every other store connection in the codebase uses — so a
    backup never takes any lock on the live source file (a plain sqlite3.connect() call would
    still be a real, if short-lived, connection to a file this function's whole job is to
    leave completely undisturbed). The destination is a throwaway file this function creates
    and populates in one call, never reopened afterward, so it connects directly via plain
    sqlite3.connect() rather than through connect() — routing it through the WAL-enabling
    helper would just leave -wal/-shm sidecars on a file with no second reader to benefit from
    WAL mode.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    src = connect(source_path, readonly=True)
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
