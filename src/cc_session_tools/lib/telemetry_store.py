"""Shared schema, CCCS_HOOKS_DIR resolution, and connection helper for
telemetry.db. Single source of truth so cccs_hooks.telemetry (writer),
cccs_hooks.telemetry_trim, cccs_hooks.telemetry_query, and
lib.scheduler.ledger (catch-up reader/writer) can never point at different
directories or apply different schemas — the exact per-module drift risk
data-stores-design-spec.md Section 7.3 calls out.

telemetry_events holds the generic PreToolUse/bash-security-review hook-fire
family. catchup_events holds typed scheduler catch-up rows (job_id, event,
owed, ran, exit_code, duration_ms, error, consecutive_failures as real
columns, not a nested-JSON blob). Both use INTEGER PRIMARY KEY AUTOINCREMENT
so row ids are monotonic and never reused, even after every row is deleted —
this is what lets lib.scheduler.ledger's catch-up cursor be `WHERE id > ?`
instead of a re-derived row-count index (the old rotation/cursor-desync
bug's root cause).
"""
from __future__ import annotations

import datetime
import os
import sqlite3
from pathlib import Path

from cc_session_tools.lib import db, paths

HOOKS_DIR_ENV = "CCCS_HOOKS_DIR"
DB_FILENAME = "telemetry.db"

_DDL = """
CREATE TABLE IF NOT EXISTS telemetry_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    hook       TEXT    NOT NULL,
    event      TEXT    NOT NULL,
    tool       TEXT    NOT NULL,
    session_id TEXT    NOT NULL,
    cwd_short  TEXT    NOT NULL,
    decision   TEXT    NOT NULL,
    cache      TEXT    NOT NULL,
    verdict    TEXT    NOT NULL,
    input_hash TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_ts ON telemetry_events(ts);
CREATE INDEX IF NOT EXISTS idx_telemetry_events_hook_decision
    ON telemetry_events(hook, decision);

CREATE TABLE IF NOT EXISTS catchup_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                   TEXT    NOT NULL,
    job_id               TEXT    NOT NULL,
    event                TEXT    NOT NULL,
    owed                 INTEGER NOT NULL,
    ran                  INTEGER NOT NULL,
    exit_code            INTEGER,
    duration_ms          INTEGER NOT NULL,
    error                TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_catchup_events_ts ON catchup_events(ts);
CREATE INDEX IF NOT EXISTS idx_catchup_events_job_id ON catchup_events(job_id);
"""

# Computed once at import time; tests override it with
# monkeypatch.setattr(telemetry_store, "_DEFAULT_HOOKS_DIR", tmp_path) to
# exercise the "CCCS_HOOKS_DIR unset" production-default path, matching the
# existing repo-wide convention for a module-level default.
_DEFAULT_HOOKS_DIR = paths.data_home()


def hooks_dir(explicit: Path | None = None) -> Path:
    """Resolve the telemetry.db directory: explicit override, else
    CCCS_HOOKS_DIR, else the module default."""
    if explicit is not None:
        return explicit
    raw = os.environ.get(HOOKS_DIR_ENV)
    return Path(raw) if raw else _DEFAULT_HOOKS_DIR


def db_path(explicit: Path | None = None) -> Path:
    return hooks_dir(explicit) / DB_FILENAME


def connect(explicit_dir: Path | None = None) -> sqlite3.Connection:
    """Open telemetry.db with the shared WAL/busy-timeout pragma set and the
    telemetry_events/catchup_events schema applied (idempotent — safe to call
    on every access, matching design-spec Section 8.3's "each script creates
    its schema on first connection" convention)."""
    return db.connect(db_path(explicit_dir), ddl=_DDL)


def checkpoint_and_vacuum(conn: sqlite3.Connection) -> None:
    """Force a WAL checkpoint then VACUUM, so a caller measuring
    db_path().stat().st_size afterwards sees space actually reclaimed from
    deleted rows. Used by telemetry_trim's --max-size enforcement."""
    db.checkpoint(conn)
    conn.execute("VACUUM")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
