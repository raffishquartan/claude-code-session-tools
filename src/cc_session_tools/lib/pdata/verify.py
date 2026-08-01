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

from cc_session_tools.lib.pdata import importers, init_paths, manifest, repository

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


def _count_base_records(
    conn: sqlite3.Connection, *, record_group: str, include_deleted: bool,
) -> int:
    """`COUNT(*)` against the base `records` table for one `record_group` — deliberately not
    `repository.list_base_records(...)` + `len(...)`, which would materialize every row's full
    `content` just to discard it, an O(row count) cost that contradicts row-count parity's own
    claim that it "doesn't grow with accumulated history" and spec G5 (doctor, and by extension
    verify's own checks, must never pay full-table-scan cost where a count suffices).
    `include_deleted=True` is what row-count-parity uses: a soft-deleted row still physically
    exists in the table (only `deleted_at` is set, spec §4.5) — only a row that has truly vanished
    from the table should ever trip this check, never an intentional `ccst pdata delete` (the
    growth-only model doesn't distinguish "grew via `add`" from "shrank via a legitimate `delete`"
    without this)."""
    if include_deleted:
        sql = "SELECT COUNT(*) AS c FROM records WHERE record_group=?"
    else:
        sql = "SELECT COUNT(*) AS c FROM records WHERE record_group=? AND deleted_at IS NULL"
    count: int = conn.execute(sql, (record_group,)).fetchone()["c"]
    return count


def check_row_count_parity(conn: sqlite3.Connection, project: str) -> list[VerifyIssue]:
    """For every db-owned entry in the project's classification proposal (spec §7.1) whose
    original file is still archived under .pdata-migrated/, recompute the row count the importer
    would have produced. Uses `importers.count_source_rows()` — the same function the init
    entry-count parity check (spec §7.1 step 4) uses — rather than `importers.import_entry()` +
    `len()`: `count_source_rows()` is built specifically to answer "how many rows should this
    source produce", cheaply (a line/row/section count), whereas `import_entry()` parses the
    entire archived file into full `ImportRow` objects (content, fields, file mtimes) only for
    every field but the count to be discarded here. Entries are grouped by `record_group` first
    and their expected counts summed *before* comparing — two entries feeding the same
    record_group (e.g. log.md + log.csv both classified into record_group="log") share one actual
    count, so comparing that shared count against each entry's expected count independently would
    let a loss that stays above the smaller entry's own threshold pass silently; comparing the
    summed expectation against the actual count once closes that gap. The actual count includes
    soft-deleted rows: a legitimate `ccst pdata delete` must never permanently trip this check —
    only a row that is actually gone from the table (never merely marked deleted_at) is evidence
    of loss. Skipped entirely if the project was never migrated (no .ccst-pdata-proposal.json) —
    nothing to compare against, which is not itself a defect."""
    project_root = init_paths.default_projects_root() / project
    proposal_path = project_root / init_paths.PROPOSAL_FILENAME
    if not proposal_path.exists():
        return []

    m = manifest.load(proposal_path)
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME

    expected_by_group: dict[str, int] = {}
    for entry in m.entries:
        if entry.classification != "db-owned":
            continue
        archived_path = archive_root / entry.path
        if not archived_path.exists():
            continue  # not yet cut over — nothing to compare against yet
        group = entry.db_group()
        expected_by_group[group] = (
            expected_by_group.get(group, 0) + importers.count_source_rows(archive_root, entry)
        )

    issues: list[VerifyIssue] = []
    for group, expected_rows in expected_by_group.items():
        actual_rows = _count_base_records(conn, record_group=group, include_deleted=True)
        if actual_rows < expected_rows:
            issues.append(VerifyIssue(
                check="row-count-parity", severity="FAIL",
                record_group=group, record_id=None,
                message=(
                    f"migrated source(s) for record_group {group!r} imply >= "
                    f"{expected_rows} row(s) (including soft-deleted), only "
                    f"{actual_rows} found — possible data loss"
                ),
            ))
    return issues
