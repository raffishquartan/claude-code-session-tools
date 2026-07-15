"""ccsched.db location and connection. Single source of truth for the scheduler
directory (env CC_SCHEDULER_DIR, else paths.data_home()), the DB path, and the
schema. Every scheduler store module opens the DB through connect() so WAL mode
and the busy-timeout come from lib.db.connect (Phase 1) — never hand-rolled."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from cc_session_tools.lib import db, paths

SCHEDULER_DIR_ENV = "CC_SCHEDULER_DIR"

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id         TEXT PRIMARY KEY,
    cadence        TEXT NOT NULL,
    coalesce_kind  TEXT NOT NULL CHECK (coalesce_kind IN ('one', 'each')),
    command        TEXT NOT NULL,
    surface        INTEGER NOT NULL,
    enabled        INTEGER NOT NULL,
    catchup_window TEXT NOT NULL,
    timeout        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_state (
    job_id               TEXT PRIMARY KEY,
    registered_at        TEXT NOT NULL,
    last_success         TEXT,
    last_attempt         TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    suspended            INTEGER NOT NULL DEFAULT 0,
    in_flight_pid        INTEGER,
    in_flight_started_at TEXT,
    in_flight_instants   INTEGER
);
CREATE TABLE IF NOT EXISTS cursors (
    session_uuid TEXT PRIMARY KEY,
    offset       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reconcile_throttle (
    session_uuid       TEXT PRIMARY KEY,
    last_reconciled_at TEXT NOT NULL
);
"""


def scheduler_dir() -> Path:
    """Directory holding ccsched.db and the .run.<job-id>.lock files. Override
    with CC_SCHEDULER_DIR (tests, non-standard setups); else paths.data_home()."""
    raw = os.environ.get(SCHEDULER_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    return paths.data_home()


def db_path() -> Path:
    return scheduler_dir() / "ccsched.db"


def connect(*, readonly: bool = False) -> sqlite3.Connection:
    """Open ccsched.db with the schema applied (WAL + busy-timeout via lib.db)."""
    return db.connect(db_path(), ddl=None if readonly else _DDL, readonly=readonly)
