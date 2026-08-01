"""Integrity-check backstop for ccst pdata (spec §6.3, §8.2).

Three checks per project, layered on top of the shipped store and (where
migration history exists) the archived migration record: row-count parity,
file_path resolution, and suspiciously-close-in-time double-updates. Results
are persisted here, never recomputed live by `ccst doctor` — a recurring
ccsched job (`pdata-verify-all`, registered in the shared
lib/scheduler/bundled_jobs.py) produces them; doctor only reads the most
recent one (verify.last_run()).

This module owns two tables of its own (pdata_verify_watermark,
pdata_verify_runs), created lazily via ensure_verify_tables() on top of the
connection repository.connect() already opens. The store, migration and
importer modules are never modified by this module — only their already-public
functions are called.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

_VERIFY_DDL = """
CREATE TABLE IF NOT EXISTS pdata_verify_watermark (
    record_id INTEGER PRIMARY KEY,
    last_seen_version INTEGER NOT NULL,
    last_seen_updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pdata_verify_runs (
    id INTEGER PRIMARY KEY,
    run_at INTEGER NOT NULL,
    full_scan INTEGER NOT NULL,
    status TEXT NOT NULL,
    issue_count INTEGER NOT NULL,
    details TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pdata_verify_runs_run_at ON pdata_verify_runs(run_at);
"""

_MAX_RETAINED_RUNS = 30
_DOUBLE_UPDATE_WINDOW_SECONDS = 6 * 60 * 60  # see plan Decision 4 — not a CLI flag

_SEVERITY_ORDER = {"OK": 0, "WARN": 1, "FAIL": 2}


@dataclass
class VerifyIssue:
    check: str
    severity: str
    record_group: str | None
    record_id: int | None
    message: str


@dataclass
class VerifySummary:
    project: str
    run_at: int
    full_scan: bool
    status: str
    issues: list[VerifyIssue] = field(default_factory=list)


def ensure_verify_tables(conn: sqlite3.Connection) -> None:
    """CREATE ... IF NOT EXISTS both verify-only tables — safe to call on
    every run, matching repository.connect()'s own connect-time DDL
    idempotency."""
    conn.executescript(_VERIFY_DDL)
