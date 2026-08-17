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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import sessions_db

_SYNCED_VERSION_KEY = "synced_version"

_LAST_ATTEMPT_VERSION_KEY = "last_attempt_version"
_LAST_ATTEMPT_AT_KEY = "last_attempt_at"
_LAST_ATTEMPT_RC_KEY = "last_attempt_rc"
_FAILURE_KEYS = (_LAST_ATTEMPT_VERSION_KEY, _LAST_ATTEMPT_AT_KEY, _LAST_ATTEMPT_RC_KEY)

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # matches sessions_db._now_iso()


@dataclass(frozen=True, slots=True)
class FailedAttempt:
    """A recorded auto-apply failure: which version was attempted, when, and
    the non-zero rc the five install steps produced."""

    version: str
    at: datetime
    rc: int


def get_failed_attempt(*, path: Path | None = None) -> FailedAttempt | None:
    """Return the last recorded failed auto-apply, or None if there isn't one.

    Read on every non-exempt ccst invocation, so it degrades to None rather
    than raising for every way the store can be unusable: no file, a
    pre-upgrade db with no install_sync table, a corrupt file (all
    sqlite3.DatabaseError, exactly as get_synced_version handles them), and a
    hand-edited row whose timestamp or rc doesn't parse. Degrading to "no
    failed attempt" means the next invocation retries the apply, which is the
    safe direction: at worst one extra 16.8 ms attempt.
    """
    try:
        conn = sessions_db.connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return None
    try:
        rows = conn.execute(
            "SELECT key, value FROM install_sync WHERE key IN (?, ?, ?)", _FAILURE_KEYS
        ).fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()

    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(_FAILURE_KEYS):
        return None
    try:
        at = datetime.strptime(values[_LAST_ATTEMPT_AT_KEY], _TS_FORMAT).replace(
            tzinfo=timezone.utc
        )
        rc = int(values[_LAST_ATTEMPT_RC_KEY])
    except ValueError:
        return None
    return FailedAttempt(version=values[_LAST_ATTEMPT_VERSION_KEY], at=at, rc=rc)


def record_failed_attempt(version: str, *, rc: int, path: Path | None = None) -> None:
    """Record that an auto-apply of `version` failed with `rc`, so
    decide_auto_sync can back off instead of retrying on every invocation."""
    now = sessions_db._now_iso()
    conn = sessions_db.connect(path=path)
    try:
        conn.executemany(
            "INSERT INTO install_sync (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            [
                (_LAST_ATTEMPT_VERSION_KEY, version, now),
                (_LAST_ATTEMPT_AT_KEY, now, now),
                (_LAST_ATTEMPT_RC_KEY, str(rc), now),
            ],
        )
        conn.commit()
    finally:
        conn.close()


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

_EXEMPT_VERBS = frozenset({"install", "uninstall"})

AUTO_SYNC_OPT_OUT_ENV = "CCST_NO_AUTO_SYNC"


def is_auto_sync_exempt(*, noun: str | None, verb: str | None, opted_out: bool) -> bool:
    """True if this invocation must not trigger an auto-apply.

    Pure, argv-and-env only - deliberately answerable without touching
    sessions.db, so ensure_synced can short-circuit an exempt caller before
    any marker read. `ccst hooks run <verb>` is the reason that property
    matters: it fires on every tool call in every open Claude Code session,
    and the 0.56 ms marker read is a cost measured per invocation, not per
    command.

    Exemptions, in order: the env opt-out (CI, bisecting, and this repo's own
    test suite); `hooks run`, which must never rewrite settings.json from
    inside a hook Claude Code invoked from settings.json mid-session;
    install-everything (would recurse), doctor (must be able to report the
    out-of-sync state rather than silently erasing it), and repair/migrate
    (the recovery tools for a broken store, which must run under any store
    state); and any `install`/`uninstall` verb, where the user is driving
    install state by hand with their own --target/--source/--hook and a
    default-target auto-apply underneath them would be self-contradictory.
    """
    if opted_out:
        return True
    if noun == "hooks" and verb == "run":
        return True
    if noun in _EXEMPT_NOUNS:
        return True
    return verb in _EXEMPT_VERBS


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
    """Record that `install-everything --apply` just succeeded for `version`,
    and clear any recorded failed auto-apply.

    Clearing here rather than in ensure_synced is deliberate: an explicit
    `ccst install-everything --apply` that succeeds must also clear the
    backoff, otherwise a user who fixes the broken step by hand keeps seeing
    doctor FAIL and the once-per-6h retry.
    """
    conn = sessions_db.connect(path=path)
    try:
        conn.execute(
            "INSERT INTO install_sync (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (_SYNCED_VERSION_KEY, version, sessions_db._now_iso()),
        )
        conn.execute(
            "DELETE FROM install_sync WHERE key IN (?, ?, ?)", _FAILURE_KEYS
        )
        conn.commit()
    finally:
        conn.close()
