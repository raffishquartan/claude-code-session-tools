"""Tracks the last ccst version for which `ccst install-everything --apply`
succeeded — lets `main()` nudge an interactive user to re-run it after an
upgrade, and lets `ccst doctor` report the same fact as a check result.

Backed by the install_sync table in sessions.db, following the same
established pattern as doctor_mutes.py (see its module docstring and
sessions_db.py's): small persistent CLI state belongs in the shared
sessions.db file, not a bespoke JSON/db file per subsystem.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cc_session_tools.lib import sessions_db

_SYNCED_VERSION_KEY = "synced_version"


def get_synced_version(*, path: Path | None = None) -> str | None:
    """Return the version `install-everything --apply` last succeeded for,
    or None if it has never been recorded.

    Covers three distinct states, all returning None: no sessions.db file at
    all (fresh machine); a sessions.db that predates this table (every
    existing installation upgrading to the version that ships this feature —
    session_tags/sessions/doctor_mutes already exist, but install_sync
    doesn't yet — connect(readonly=True) skips DDL by design, since it must
    not create/migrate a store it's only meant to read, so this case reaches
    the SELECT and must be caught there too, not just around connect()
    itself); and a sessions.db that is corrupt/not a valid SQLite file at
    all, which callers of this function (main()'s interactive gate among
    them) must be able to survive without crashing - a corrupt db is
    reported the same as "never synced" rather than propagating an
    exception. sqlite3.DatabaseError is the shared parent of
    OperationalError ("no such table") and the corrupt-file case, so
    catching it alone covers both without a second except clause.
    """
    try:
        conn = sessions_db.connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(
            "SELECT value FROM install_sync WHERE key = ?", (_SYNCED_VERSION_KEY,)
        ).fetchone()
        return row["value"] if row is not None else None
    except sqlite3.DatabaseError:
        return None  # "no such table: install_sync" (pre-upgrade db) or a corrupt file
    finally:
        conn.close()


_EXEMPT_NOUNS = frozenset({"install-everything", "doctor", "repair", "migrate"})


def should_block_for_unsynced_install(
    *,
    noun: str | None,
    verb: str | None,
    installed_version: str,
    synced_version: str | None,
    is_interactive: bool,
) -> bool:
    """True if main() should abort this invocation with an install-everything
    nudge instead of dispatching it.

    is_interactive must be False for every automated caller (a Claude Code
    hook via `ccst hooks run`, a ccsched job, any future scheduled/scripted
    caller) - this is the primary safety property, checked first. Exempt
    nouns are never blocked regardless of interactivity, so the user always
    has a way to see or fix the state this function is protecting against:
    install-everything (the fix), doctor (the diagnostic tool), repair
    ("Repair known sessions.db/store corruption" - the exact tool needed
    when the sync marker's own store is what's broken), and migrate (this
    repo's own doctor output tells users to run `ccst migrate all` "from a
    plain terminal" when a legacy-data migration is pending - blocking that
    instruction would be self-defeating). `hooks run` is additionally exempt
    by name, belt-and-braces alongside is_interactive always being False for
    it in practice - the one path this function must never block under any
    circumstance.
    """
    if not is_interactive:
        return False
    if noun in _EXEMPT_NOUNS:
        return False
    if noun == "hooks" and verb == "run":
        return False
    return synced_version != installed_version


def record_synced(version: str, *, path: Path | None = None) -> None:
    """Record that `install-everything --apply` just succeeded for `version`."""
    conn = sessions_db.connect(path=path)
    try:
        conn.execute(
            "INSERT INTO install_sync (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (_SYNCED_VERSION_KEY, version, sessions_db._now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
