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
from pathlib import Path

_BUSY_TIMEOUT_MS = 5000
_MIN_SQLITE_VERSION = (3, 35, 0)


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
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=_BUSY_TIMEOUT_MS / 1000, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_MS / 1000, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            conn.execute("PRAGMA foreign_keys=ON")
            if ddl:
                conn.executescript(ddl)
                conn.commit()
        except BaseException:
            conn.close()
            raise

    conn.row_factory = sqlite3.Row
    return conn


def checkpoint(conn: sqlite3.Connection) -> None:
    """Force a WAL checkpoint. Call before any filesystem-level copy of a live .db file."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def backup_to(source_path: Path, dest_path: Path) -> None:
    """Safely copy a live WAL-mode .db file using SQLite's own backup API.

    Safe against concurrent writers on source_path — no manual checkpoint or
    cp needed (sqlite3.Connection.backup() handles this internally). Used by
    `ccst backup run` (Phase 7) and by migration scripts' pre-cutover safety
    copies.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source_path))
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
