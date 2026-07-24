"""Typed catch-up event store over telemetry.db's catchup_events table:
write one row per sweep action, and read recent/since-cursor rows back for
`ccsched status` and the surfacing pass (surface.py).

Catch-up rows are typed columns (job_id, event, owed, ran, exit_code,
duration_ms, error, consecutive_failures) in their own table — never a
nested-JSON blob — so this module's own SQL does the filtering instead of
parsing JSON on every read.

The cursor this module hands back is catchup_events.id: an AUTOINCREMENT
monotonic row id that is never reused, including across DELETE-based trims
(ccst telemetry trim). This closes the old rotation/cursor-desync bug: the
old cursor was a row-count index into a sequence re-derived by re-filtering
the flat file on every read, so a rotation could make a stale stored count
silently swallow genuinely-new post-rotation rows. `WHERE id > ?` against a
column whose values are never reused cannot desync this way regardless of
what a trim deletes underneath it.
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from cc_session_tools.lib import telemetry_store


class LedgerEvent(str, Enum):
    LAUNCH = "launch"
    RUN = "run"
    BACKFILL = "backfill"
    SKIP_EXPIRED = "skip_expired"
    DEFER = "defer"
    FAIL = "fail"
    SUSPEND = "suspend"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    job_id: str
    event: LedgerEvent
    owed: int
    ran: int
    exit_code: int | None
    duration_ms: int
    error: str | None
    consecutive_failures: int = 0


def _hooks_dir() -> Path:
    """The telemetry.db directory: the CCCS_HOOKS_DIR override when set,
    else telemetry_store's default. Kept as a thin wrapper so catchup.py's
    existing ``ledger._hooks_dir()`` call keeps working unchanged."""
    return telemetry_store.hooks_dir()


def record(entry: LedgerEntry) -> None:
    """Insert one catchup_events row. Never raises."""
    try:
        conn = telemetry_store.connect()
        try:
            conn.execute(
                "INSERT INTO catchup_events "
                "(ts, job_id, event, owed, ran, exit_code, duration_ms, error, consecutive_failures) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    telemetry_store.now_iso(), entry.job_id, entry.event.value,
                    entry.owed, entry.ran, entry.exit_code, entry.duration_ms,
                    entry.error, entry.consecutive_failures,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as e:
        print(f"[telemetry-warn] ledger write failed: {e}", file=sys.stderr)


def read_recent(job_id: str | None = None, *, limit: int = 50) -> list[dict[str, object]]:
    """Return up to ``limit`` recent catch-up rows, oldest-first within that
    slice, optionally filtered by job_id."""
    conn = telemetry_store.connect()
    try:
        if job_id is not None:
            rows = conn.execute(
                "SELECT * FROM catchup_events WHERE job_id = ? ORDER BY id DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM catchup_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def read_since(offset: int) -> tuple[list[dict[str, object]], int]:
    """Catch-up rows with id > offset, oldest-first, plus the new offset
    (the highest id seen, or the unchanged offset if nothing is new). Used by
    the surface/reap phase (§9.3)."""
    conn = telemetry_store.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM catchup_events WHERE id > ? ORDER BY id", (offset,)
        ).fetchall()
        new_offset = rows[-1]["id"] if rows else offset
        return [dict(r) for r in rows], new_offset
    finally:
        conn.close()


def current_offset() -> int:
    """The current max catchup_events id (0 if empty). Used to seed a
    brand-new session's cursor (§9.3) so its first digest reflects only
    activity from this point forward, not pre-existing history."""
    conn = telemetry_store.connect()
    try:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM catchup_events").fetchone()
        return int(row["m"])
    finally:
        conn.close()
