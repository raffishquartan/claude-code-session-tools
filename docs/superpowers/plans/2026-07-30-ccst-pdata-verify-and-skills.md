# ccst pdata verify + scheduling + pm- skills (Plan D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `ccst pdata verify` — the integrity-check backstop (spec §6.3) that catches what
prevention (Plan A's optimistic concurrency, Plan B's non-destructive migration) doesn't fully
rule out — plus the recurring `ccsched` job that runs it automatically and feeds `ccst doctor`
(spec §8.2), and the two `pm-`-prefixed skills that supply judgement at the two moments this
system needs it most: designing a new `record_group` (`pm-pdata-schema-design`) and reconciling a
version conflict (`pm-pdata-conflict-resolution`) (spec §8.1).

**Architecture:** A new `src/cc_session_tools/lib/pdata/verify.py` module runs three read-mostly
checks against a project's already-shipped `ccst pdata` store (Plan A) and, where migration
history exists, against Plan B's archived migration record — it adds **zero** changes to Plan A's
or Plan B's shipped files, calling only their already-planned public functions
(`repository.connect`/`list_base_records`/`list_record_groups`,
`init_paths.default_projects_root`, `manifest.load`, `importers.count_source_rows`). Every check
here reuses the one `conn` its caller already opened rather than opening a second connection of
its own (see Task 3/Task 4's use of `repository.list_record_groups(conn)` in place of
`service.schema_list(project=project)`). Verify owns two
small tables of its own (`pdata_verify_watermark`, `pdata_verify_runs`), created lazily via its own
`CREATE TABLE IF NOT EXISTS` DDL on top of the connection Plan A's `repository.connect()` already
opens — never a modification to Plan A's `_BASE_DDL`. Results are persisted, never recomputed live
by `ccst doctor` (§8.2's own framing: "feeding failures into `ccst doctor` ... rather than only
running silently in the background" — a periodic job produces the data, doctor only reads it).
The recurring run is provisioned as one `ccsched` job (`pdata-verify-all`, one job iterating every
project — the spec's own "one job per project (or one job iterating all projects)" allows either;
this plan picks the latter to avoid N job-provisioning calls growing with project count), matching
the spec's "must be provisioned automatically at install time, not a manual one-off" requirement.
Per Plan C's own binding "Post-plan note for whoever picks up Plan D"
(2026-07-30-ccst-pm-update-central-files.md), provisioning is **not** a Plan-D-owned mechanism:
this plan appends one `BundledJob` entry to Plan C's shared `lib/scheduler/bundled_jobs.py`
registry (`BUNDLED_CCSCHED_JOBS`) and relies entirely on Plan C's already-generic `ccst
ccsched-jobs install` (wired into `ccst install-everything`'s "Scheduled jobs" step) and
`check_ccsched_job_registered` doctor check — this plan defines no `verify_job.py` module, no
`install-everything` step of its own, and no job-registration doctor check of its own. The two
skills are plain `SKILL.md` judgement documents — no new CLI surface — living under
`skills/pm-pdata-schema-design/` and
`skills/pm-pdata-conflict-resolution/`, auto-discovered by the existing `ccst skills install`
scanner (any `skills/<name>/SKILL.md` is picked up; no registration list to update).

**Tech Stack:** Python 3.11+, stdlib `sqlite3` (via Plan A's `lib/pdata/repository.connect()`),
`json`, `time`, `argparse`, `pytest` (subprocess CLI tests matching
`tests/test_ccst_pdata_cli.py`), Markdown (the two `SKILL.md` files).

---

## Scope

**In scope** (spec `2026-07-26-per-project-data-store-spec.md` §5's `verify` entry, §6.3, §8.1,
§8.2 — read in full before writing code):

- `ccst pdata verify --project <name> [--full] | --all-projects` (spec §5): row-count parity against
  still-present migration-archived originals, `file_path` resolution, rows updated by two sessions
  within a suspiciously short window (§6.3) — plus persisting each run's result so `ccst doctor`
  can report it cheaply (G5: doctor must never itself pay the cost of a full verify pass).
- The `ccsched` job that runs `ccst pdata verify --all-projects` periodically, provisioned automatically at
  install time (§8.2) by way of an entry this plan adds to Plan C's shared `BUNDLED_CCSCHED_JOBS`
  registry, and the `ccst doctor` check that surfaces its last persisted result (§8.2's "feeding
  failures into `ccst doctor`").
- `pm-pdata-schema-design` and `pm-pdata-conflict-resolution` — their triggers and
  responsibilities exactly as described in §8.1: the first is invoked before writing a genuinely
  new kind of structured data (existing group vs. new group vs. extension table vs. free-text
  `content`); the second is invoked when `ccst pdata update`/`delete` reports a version conflict
  (§6.2), taking the CLI's current-vs-attempted diff to Chris for reconciliation.

**Explicitly out of scope:**

- `ccst pdata export` (spec §5 "Project lifecycle"). **Not designed by this plan, nor by any of
  Plans A/B/C.** This is a real gap: every other `pdata` subcommand the spec names now has a plan
  behind it except `export`. Flagging it here as the concrete thing a future **Plan E** needs to
  cover — read-only, point-in-time CSV/MD rendering of one `record_group`, per spec §5's own
  description ("courtesy copy... never the source of truth").
- Everything Plan A ships (`add`/`get`/`list`/`query`/`update`/`delete`/`restore`,
  `schema list`/`show`/`add-field`, the base schema, extension tables, the naming/repository/
  service/formatting modules) and everything Plan B ships (`ccst pdata init`, the classification
  manifest, the five import strategies, backup/cutover, the pending-migration doctor check,
  `pm-project-init`). This plan calls their public functions but modifies none of their files.
- Everything else Plan C covers (the `pm-update-central-files` rename/relocation, the
  `session_output.py` reconciliation module, `ccst pdata reconcile-session-output`) — this plan
  reads only the one piece of Plan C it has a binding dependency on: the shared
  `lib/scheduler/bundled_jobs.py`/`BUNDLED_CCSCHED_JOBS` registry (Plan C Task 7) and the generic
  `ccst ccsched-jobs install`/`check_ccsched_job_registered` machinery built on top of it (Plan C
  Tasks 8-9), per Plan C's own binding "Post-plan note for whoever picks up Plan D". This plan's
  version-bump task still reads the on-disk version at implementation time rather than assuming a
  specific predecessor number (see Versioning below), since the exact version Plan C lands at is
  independent of this dependency.
- Per-project migration content itself (which files become which `record_group`s for any real
  `~/cc/<project>`) — Plan B's own scope boundary, unaffected by this plan.
- A general-purpose backup mechanism for per-project `.db` files, a web UI, and the other spec §9
  backlog items — unrelated to verify/scheduling/these two skills.

## Versioning (read before the last task)

Per this repo's `CLAUDE.md` version policy, a major bump is reserved for changes that make
existing on-disk data unreadable by old code until a migration runs. This plan adds a strictly
additive CLI verb (`ccst pdata verify`), two new tables that no prior version of `ccst` ever wrote
to (so there is nothing for an old code path to fail to read), one new entry in Plan C's shared
`BUNDLED_CCSCHED_JOBS` list, a new `ccst doctor` check, and two new skill directories. Nothing
existing changes shape. That makes this plan's own contribution a **minor** bump. This plan must
land after **both** Plan B and Plan C: Task 2's `check_row_count_parity` imports and calls Plan
B's `importers`/`init_paths`/`manifest` modules directly (and Task 2's tests import Plan B's
`ManifestEntry`/`manifest.save`), so Plan B's files must already exist on disk before Task 2 can
be written or run, exactly as Plan C's `bundled_jobs.py` registry must already exist before Task 8
can be written or run. The exact resulting version number is still **not** hardcoded here — the
final task reads `pyproject.toml`'s version at the time this plan is actually executed and bumps
the minor component by one from whatever is on disk then (matching Plan A's `1.0.0`→`1.1.0` and
Plan B's `1.1.0`→`1.2.0` pattern, continued from wherever the repo actually stands once A/B/C have
landed).

## Necessary implementation decisions beyond the spec's literal text

The spec (read in full before writing code) leaves several verify-specific mechanics implicit.
These are binding for this plan:

1. **Row-count-parity's comparison source is Plan B's own persisted artifacts, reused verbatim —
   never re-derived.** Plan B's `.ccst-pdata-proposal.json` (never deleted post-cutover) records
   exactly which files were migrated, with which `record_group`/`strategy`/`fields`/`delimiter`;
   its `.pdata-migrated/<path>` archive keeps the original bytes forever (manual-delete-only, spec
   §7.1 step 7). Re-running Plan B's own `importers.count_source_rows(archive_root, entry)`
   against the still-archived file reproduces the *exact* row count that migration originally
   produced, including any custom `--delimiter` a human set on that one entry — no guessing, no
   reimplementing Plan B's parsing logic a second time with room to drift, and no paying the cost
   of fully parsing the file into `ImportRow` objects (`count_source_rows` is Plan B's own
   cheap-count counterpart to `import_entry`, built for exactly this "recompute expected row count
   from source" need, spec §7.1 step 4). A project with no `.ccst-pdata-proposal.json` (never
   migrated — every row came from plain `ccst pdata add` calls) skips this check entirely; there
   is nothing to compare against and that is not itself a defect.
2. **Row-count parity is `actual >= expected`, never `==`.** A `record_group` legitimately grows
   after migration via ordinary `ccst pdata add` calls. The check only guards against the row
   count *falling below* what migration originally produced — that direction is the only one that
   can mean silent data loss (G1); more rows than expected is normal, healthy growth, not a defect.
   Two refinements this implies: (a) when more than one manifest entry feeds the same
   `record_group` (e.g. `log.md` + `log.csv` both classified into `record_group="log"` — a real
   fixture shape Plan B's own migration tests exercise), their expected row counts are **summed**
   before comparing against that group's one shared actual count — comparing the shared count
   against each entry's expectation independently would let a loss above the smaller entry's own
   threshold pass unnoticed; (b) the actual count includes soft-deleted rows (`include_deleted=
   True`) — a soft-deleted row (spec §4.5) still physically exists in the table, so an ordinary,
   legitimate `ccst pdata delete` must never permanently trip this check. Only a row that has
   truly vanished from the table (not merely marked `deleted_at`) counts as evidence of loss.
3. **`file_path` resolution needs the project root, resolved read-only.** Plan B's own
   `init_paths.resolve_project_root()` creates the directory if missing (right for `ccst pdata
   init`, wrong for a read-only integrity check that must never have a *side effect* of creating
   directories). This plan uses `init_paths.default_projects_root() / project` directly — a
   project whose folder genuinely does not exist yet simply fails every non-null `file_path` check
   for that project (correctly: a `file_path` cannot resolve under a directory that isn't there).
4. **Suspicious-close-in-time double-updates are detected via a persisted per-record watermark,
   not a stored update history.** The base `records` schema (Plan A) has no per-update audit log —
   only the current `version`/`updated_at`. Verify keeps its own tiny
   `pdata_verify_watermark(record_id, last_seen_version, last_seen_updated_at)` table, updated on
   every run. A record is flagged when, compared to what verify saw last time, `version` has
   advanced by 2 or more *and* `updated_at` has moved by no more than
   `_DOUBLE_UPDATE_WINDOW_SECONDS` (6 hours, a constant, not a CLI flag — spec §6.3 doesn't call
   for a tunable threshold and this backstop is explicitly a secondary net, not the primary
   defense) — i.e. at least two updates landed inside one polling interval. This is a WARN, not a
   FAIL: per spec §6.3 it "deserves a human glance," it is not proof of a problem (the two updates
   might both have been correct and intentional).
5. **Row-count parity and file_path resolution are asymmetric in whether `--full` matters.**
   Row-count parity is bounded by the number of migrated `record_group`s (fixed, small, doesn't
   grow with accumulated history — G5) and always runs in full regardless of `--full`.
   `file_path` resolution and the double-update watermark check are row-level and *do* scale with
   accumulated row count, so both honor the incremental cursor: by default only rows with
   `updated_at` newer than the last completed run are examined; `--full` widens this to every row.
   The double-update watermark table itself is never reset by `--full` — `--full` only widens
   *which rows get compared against it this run*, it doesn't erase prior memory.
6. **`ccst doctor` never triggers a verify run.** `verify.last_run(project)` only reads the most
   recently persisted `pdata_verify_runs` row; if none exists yet (job hasn't fired, or this
   project has no `.db` at all), doctor WARNs "not yet run" — the same "unremarkable, not itself a
   defect" treatment `check_pending_data_store_migration` already established for "migration
   hasn't happened yet" (this repo's own precedent, not a new pattern invented here).
7. **Project discovery for `--all-projects` and for doctor is "every `.db` under `project_db_dir()`", not
   "every directory under `CCST_PROJECTS_ROOT`".** A project can have a `.db` with no on-disk
   folder yet (fresh via `ccst pdata add` before ever running `ccst pdata init`), and the inverse
   (a folder with no `.db`, e.g. `biz`/`fire` per the spec's own inventory) is not this command's
   concern — `ccst pdata verify` only has something to say about a project that already has a
   store. This applies equally to a single named `--project`: `run_verify()` raises rather than
   letting `repository.connect()`'s own `CREATE TABLE IF NOT EXISTS` side effect fabricate a
   brand-new empty store for a project that never had one and report it "clean" — the same
   "already has a store" precondition `--all-projects`'s own discovery already enforces by construction.
8. **Exit codes**, matching `ccst doctor`'s own convention (0 clean, 1 any WARN/FAIL, nothing
   reserved for "not found" since a missing project is itself just an empty result, not an error):
   `0` if every verified project's run status is OK, `1` if any project's status is WARN or FAIL,
   `2` for a CLI/validation error (bad `--project` name, or `--all-projects` with zero project `.db` files
   found — nothing to verify is worth flagging distinctly from "verified and clean").
9. **The `ccsched` job runs at `daily@03:00`, `--coalesce one`, `--no-surface`.** Daily is frequent
   enough that G1-relevant drift gets caught within a day without adding meaningful load (row-level
   checks are incremental per Decision 5); `03:00` avoids contending with interactive session work;
   `coalesce one` is correct for a "check current state" job (matches this repo's own
   `manage-recurring-cc-jobs-using-ccsched` skill guidance); `--no-surface` because results are
   meant to reach `ccst doctor`, not interrupt a session directly, per spec §8.2's own framing.
10. **Job provisioning is one more entry in Plan C's shared `BUNDLED_CCSCHED_JOBS` list, never a
    Plan-D-owned provisioning mechanism.** Plan C's own "Post-plan note for whoever picks up Plan
    D" (2026-07-30-ccst-pm-update-central-files.md) is explicit and binding: `lib/scheduler/
    bundled_jobs.py` is the single place a new install-time `ccsched` job gets declared, and
    `ccst ccsched-jobs install` / `check_ccsched_job_registered` (both Plan C) already iterate that
    whole list. A Plan-D-owned `verify_job.py` module with its own `ensure_pdata_verify_job()`, its
    own `registry.add_job()` call, its own `install-everything` step, or its own job-registration
    doctor check would each independently duplicate machinery Plan C already built generically —
    exactly the "two competing mechanisms" Plan C's note warns against. This plan therefore adds
    exactly one `BundledJob(...)` tuple to `BUNDLED_CCSCHED_JOBS` and nothing else.

## File structure

```
src/cc_session_tools/lib/pdata/
  verify.py            NEW — the three checks, run_verify()/last_run()/discover_projects(),
                        VerifyIssue/VerifySummary dataclasses, the two verify-only tables' DDL.
                        Imports Plan A's store/repository and Plan B's init_paths/manifest/
                        importers — read-only reuse, no modifications to those files.

src/cc_session_tools/lib/scheduler/bundled_jobs.py  (modified — Plan C's file: one new BundledJob
                                                     entry, "pdata-verify-all", appended to
                                                     BUNDLED_CCSCHED_JOBS. No other change.)
src/cc_session_tools/cli/ccst.py     (modified — new "pdata verify" verb, module docstring update.
                                      No install-everything change — Plan C's "Scheduled jobs" step
                                      already provisions every BUNDLED_CCSCHED_JOBS entry.)
src/cc_session_tools/lib/doctor.py   (modified — new check_pdata_verify(), wired into
                                      run_all_checks() and _cmd_doctor(). This is a distinct check
                                      from Plan C's check_ccsched_job_registered() — this one
                                      reports the *content* of verify's last run, not whether the
                                      job is registered.)

tests/pdata/
  test_verify.py        NEW

tests/test_scheduler_bundled_jobs.py      (modified — Plan C's file: two new assertions for the
                                           pdata-verify-all entry)
tests/test_ccst_pdata_verify_cli.py       NEW (subprocess CLI tests, one file per
                                           tests/test_ccst_pdata_cli.py precedent)
tests/test_ccst_doctor.py                 (modified — check_pdata_verify tests)

skills/pm-pdata-schema-design/SKILL.md          NEW
skills/pm-pdata-conflict-resolution/SKILL.md    NEW

pyproject.toml   (modified — version bump, last task)
CHANGELOG.md     (modified — [Unreleased] entry, last task)
```

---

## Task 1: `lib/pdata/verify.py` — verify-only tables + dataclasses + `ensure_verify_tables`

**Files:**
- Create: `src/cc_session_tools/lib/pdata/verify.py`
- Create: `tests/pdata/test_verify.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pdata/test_verify.py
from __future__ import annotations

import pytest

from cc_session_tools.lib.pdata import repository, verify


def test_ensure_verify_tables_creates_watermark_and_runs_tables(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        verify.ensure_verify_tables(conn)
        tables = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "pdata_verify_watermark" in tables
        assert "pdata_verify_runs" in tables
    finally:
        conn.close()


def test_ensure_verify_tables_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        verify.ensure_verify_tables(conn)
        verify.ensure_verify_tables(conn)  # must not raise
    finally:
        conn.close()


def test_verify_issue_and_summary_are_plain_dataclasses():
    issue = verify.VerifyIssue(
        check="file-path-resolution", severity="FAIL", record_group="filings",
        record_id=1, message="broken",
    )
    summary = verify.VerifySummary(
        project="demo", run_at=1000, full_scan=False, status="FAIL", issues=[issue],
    )
    assert summary.issues[0].message == "broken"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/chris/repos/claude-code-session-tools/.worktrees/pdata-core && uv run pytest tests/pdata/test_verify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.verify'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/cc_session_tools/lib/pdata/verify.py
"""Integrity-check backstop for ccst pdata (spec §6.3, §8.2).

Three checks per project, layered on top of Plan A's shipped store and (where
migration history exists) Plan B's archived migration record: row-count
parity, file_path resolution, and suspiciously-close-in-time double-updates.
Results are persisted here, never recomputed live by `ccst doctor` — a
recurring ccsched job (`pdata-verify-all`, registered in Plan C's shared
lib/scheduler/bundled_jobs.py) produces them; doctor only reads the most
recent one (verify.last_run()).

This module owns two tables of its own (pdata_verify_watermark,
pdata_verify_runs), created lazily via ensure_verify_tables() on top of the
connection Plan A's repository.connect() already opens. Plan A's and Plan
B's shipped files are never modified by this module — only their already-
public functions are called.
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
    every run, matching Plan A's own connect()-time DDL idempotency."""
    conn.executescript(_VERIFY_DDL)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_verify.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/verify.py tests/pdata/test_verify.py
git commit -m "feat(pdata-verify): add verify-only tables and result dataclasses"
```

---

## Task 2: `verify.py` — row-count-parity check (reuses Plan B's manifest/importers, read-only)

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/verify.py`
- Modify: `tests/pdata/test_verify.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_verify.py
import json

from cc_session_tools.lib.pdata import init_paths, manifest, repository, service, verify
from cc_session_tools.lib.pdata.manifest import Manifest, ManifestEntry


def _write_proposal(project_root, entries):
    project_root.mkdir(parents=True, exist_ok=True)
    manifest.save(Manifest(project=project_root.name, entries=entries),
                  project_root / init_paths.PROPOSAL_FILENAME)


def test_row_count_parity_skips_project_with_no_migration_history(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()


def test_row_count_parity_ok_when_counts_match(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                           record_group="ideas", strategy="csv-rows")
    _write_proposal(project_root, [entry])
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True)
    (archive_root / "ideas.csv").write_text("idea\nfirst\nsecond\n")

    service.add_record(project="demo", record_group="ideas", content="first",
                        file_path=None, fields={}, created_at=1)
    service.add_record(project="demo", record_group="ideas", content="second",
                        file_path=None, fields={}, created_at=2)

    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()


def test_row_count_parity_fails_when_rows_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                           record_group="ideas", strategy="csv-rows")
    _write_proposal(project_root, [entry])
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True)
    (archive_root / "ideas.csv").write_text("idea\nfirst\nsecond\n")

    service.add_record(project="demo", record_group="ideas", content="first",
                        file_path=None, fields={}, created_at=1)
    # "second" was never inserted — simulates a silent write-loss bug.

    conn = repository.connect("demo")
    try:
        issues = verify.check_row_count_parity(conn, "demo")
        assert len(issues) == 1
        assert issues[0].severity == "FAIL"
        assert issues[0].check == "row-count-parity"
        assert issues[0].record_group == "ideas"
    finally:
        conn.close()


def test_row_count_parity_ok_when_more_rows_than_migrated(monkeypatch, tmp_path):
    """A record_group is allowed to grow after migration via ordinary
    ccst pdata add calls (plan Decision 2) — more rows than expected is
    healthy growth, not a defect."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                           record_group="ideas", strategy="csv-rows")
    _write_proposal(project_root, [entry])
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True)
    (archive_root / "ideas.csv").write_text("idea\nfirst\n")

    service.add_record(project="demo", record_group="ideas", content="first",
                        file_path=None, fields={}, created_at=1)
    service.add_record(project="demo", record_group="ideas", content="added later",
                        file_path=None, fields={}, created_at=2)

    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()


def test_row_count_parity_skips_entries_not_yet_cut_over(monkeypatch, tmp_path):
    """An entry classified db-owned in the proposal but with no archived
    counterpart yet (dry-run reviewed but --write hasn't cut it over, or it
    was a --rehearse-only run) has nothing to compare against."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                           record_group="ideas", strategy="csv-rows")
    _write_proposal(project_root, [entry])
    # No .pdata-migrated/ideas.csv written — never cut over.

    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()


def test_row_count_parity_sums_expected_across_entries_sharing_a_record_group(
    monkeypatch, tmp_path,
):
    """log.md + log.csv both mapping to record_group="log" is a real fixture shape
    Plan B's own migration tests exercise (2026-07-30-ccst-pdata-init-migration.md
    line ~1168/1193). Parity must compare the *summed* expected row count across every
    entry that feeds the group against that group's actual count once — comparing the
    shared actual count against each entry's expected count independently would let a
    loss that stays above the smaller entry's own threshold pass silently."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entries = [
        ManifestEntry(path="log.md", classification="db-owned", record_group="log",
                      strategy="whole-file"),
        ManifestEntry(path="log.csv", classification="db-owned", record_group="log",
                      strategy="csv-rows"),
    ]
    _write_proposal(project_root, entries)
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True)
    (archive_root / "log.md").write_text("one whole-file row")  # whole-file -> 1 row
    (archive_root / "log.csv").write_text("entry\nfirst\nsecond\nthird\n")  # csv-rows -> 3 rows

    # Originally migrated total for record_group "log": 1 (log.md) + 3 (log.csv) = 4 rows.
    # Only 3 active rows survive below — that is >= log.md's own expected (1) and >= log.csv's
    # own expected (3) when checked independently, so the pre-fix per-entry comparison would
    # report this project clean. The correct comparison is 3 actual < 4 summed-expected -> FAIL.
    for content in ("first", "second", "third"):
        service.add_record(project="demo", record_group="log", content=content,
                            file_path=None, fields={}, created_at=1)

    conn = repository.connect("demo")
    try:
        issues = verify.check_row_count_parity(conn, "demo")
        assert len(issues) == 1
        assert issues[0].record_group == "log"
    finally:
        conn.close()


def test_row_count_parity_not_tripped_by_a_legitimate_delete(monkeypatch, tmp_path):
    """A row that was part of the originally-migrated count can later be soft-deleted by an
    ordinary `ccst pdata delete` (Plan A, spec §4.5) without ever failing parity again — a
    soft-deleted row still physically exists in the table (only deleted_at is set), so it must
    still count toward "not lost". Excluding deleted rows from the actual count (as an
    `include_deleted=False` query would) makes every legitimate delete of an originally-migrated
    row an unfixable false-positive FAIL forever after, which contradicts Decision 2's
    grows-only-shrinks-on-real-loss model."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                           record_group="ideas", strategy="csv-rows")
    _write_proposal(project_root, [entry])
    archive_root = project_root / init_paths.MIGRATED_ARCHIVE_DIRNAME
    archive_root.mkdir(parents=True)
    (archive_root / "ideas.csv").write_text("idea\nfirst\nsecond\n")

    record = service.add_record(project="demo", record_group="ideas", content="first",
                                 file_path=None, fields={}, created_at=1)
    service.add_record(project="demo", record_group="ideas", content="second",
                        file_path=None, fields={}, created_at=2)
    service.delete_record(project="demo", record_id=record.id, expected_version=1)

    conn = repository.connect("demo")
    try:
        assert verify.check_row_count_parity(conn, "demo") == []
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_verify.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'check_row_count_parity'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/verify.py

from cc_session_tools.lib.pdata import importers, init_paths, manifest, repository


def _count_base_records(
    conn: sqlite3.Connection, *, record_group: str, include_deleted: bool,
) -> int:
    """`COUNT(*)` against the base `records` table for one `record_group` — deliberately not
    `repository.list_base_records(...)` + `len(...)`, which would materialize every row's full
    `content` just to discard it, an O(row count) cost that contradicts this plan's own Decision
    5 claim that row-count parity "doesn't grow with accumulated history" and spec G5 (doctor,
    and by extension verify's own checks, must never pay full-table-scan cost where a count
    suffices). `include_deleted=True` is what row-count-parity uses: a soft-deleted row still
    physically exists in the table (only `deleted_at` is set, spec §4.5) — only a row that has
    truly vanished from the table should ever trip this check, never an intentional `ccst pdata
    delete` (plan Decision 2's growth-only model doesn't distinguish "grew via `add`" from
    "shrank via a legitimate `delete`" without this)."""
    if include_deleted:
        sql = "SELECT COUNT(*) AS c FROM records WHERE record_group=?"
    else:
        sql = "SELECT COUNT(*) AS c FROM records WHERE record_group=? AND deleted_at IS NULL"
    return conn.execute(sql, (record_group,)).fetchone()["c"]


def check_row_count_parity(conn: sqlite3.Connection, project: str) -> list[VerifyIssue]:
    """For every db-owned entry in the project's classification proposal
    (Plan B, spec §7.1) whose original file is still archived under
    .pdata-migrated/, recompute the row count Plan B's own importer would
    have produced. Uses Plan B's `importers.count_source_rows()` — the same
    function Plan B's own init_service entry-count parity check (spec §7.1
    step 4) uses — rather than `importers.import_entry()` + `len()`:
    `count_source_rows()` is built specifically to answer "how many rows
    should this source produce", cheaply (a line/row/section count), whereas
    `import_entry()` parses the entire archived file into full `ImportRow`
    objects (content, fields, file mtimes) only for every field but the
    count to be discarded here. Entries are grouped by `record_group` first
    and their expected counts summed *before* comparing — two entries
    feeding the same record_group (e.g. log.md + log.csv both classified
    into record_group="log", a real fixture shape Plan B's own migration
    tests exercise) share one actual count, so comparing that shared count
    against each entry's expected count independently would let a loss that
    stays above the smaller entry's own threshold pass silently; comparing
    the summed expectation against the actual count once closes that gap.
    The actual count includes soft-deleted rows (plan Decision 2 extended):
    a legitimate `ccst pdata delete` must never permanently trip this
    check — only a row that is actually gone from the table (never merely
    marked deleted_at) is evidence of loss. Skipped entirely if the project
    was never migrated (no .ccst-pdata-proposal.json) — nothing to compare
    against, which is not itself a defect (plan Decision 1)."""
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_verify.py -v`
Expected: PASS (10 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/verify.py tests/pdata/test_verify.py
git commit -m "feat(pdata-verify): add row-count-parity check against migrated originals"
```

---

## Task 3: `verify.py` — `file_path` resolution check

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/verify.py`
- Modify: `tests/pdata/test_verify.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_verify.py

def test_file_path_resolution_ok_when_file_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "a.pdf").write_bytes(b"%PDF-1.4")

    service.add_record(project="demo", record_group="filings", content="x",
                        file_path="a.pdf", fields={}, created_at=1)

    conn = repository.connect("demo")
    try:
        assert verify.check_file_path_resolution(conn, "demo", since=None) == []
    finally:
        conn.close()


def test_file_path_resolution_fails_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    # a.pdf deliberately not created

    service.add_record(project="demo", record_group="filings", content="x",
                        file_path="a.pdf", fields={}, created_at=1)

    conn = repository.connect("demo")
    try:
        issues = verify.check_file_path_resolution(conn, "demo", since=None)
        assert len(issues) == 1
        assert issues[0].severity == "FAIL"
        assert issues[0].check == "file-path-resolution"
    finally:
        conn.close()


def test_file_path_resolution_ignores_rows_with_no_file_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="ccst-ideas", content="an idea",
                        file_path=None, fields={}, created_at=1)
    conn = repository.connect("demo")
    try:
        assert verify.check_file_path_resolution(conn, "demo", since=None) == []
    finally:
        conn.close()


def test_file_path_resolution_honors_since_cursor(monkeypatch, tmp_path):
    """A row updated before `since` is skipped even if its file is missing —
    incremental scope (plan Decision 5); --full passes since=None to check
    every row regardless of age."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)

    service.add_record(project="demo", record_group="filings", content="old",
                        file_path="missing-old.pdf", fields={}, created_at=100)
    service.add_record(project="demo", record_group="filings", content="new",
                        file_path="missing-new.pdf", fields={}, created_at=200)

    conn = repository.connect("demo")
    try:
        issues = verify.check_file_path_resolution(conn, "demo", since=150)
        assert len(issues) == 1
        assert "missing-new.pdf" in issues[0].message

        issues_full = verify.check_file_path_resolution(conn, "demo", since=None)
        assert len(issues_full) == 2
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_verify.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'check_file_path_resolution'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/verify.py
# (no new import needed — repository is already imported by Task 2)


def check_file_path_resolution(
    conn: sqlite3.Connection, project: str, *, since: int | None,
) -> list[VerifyIssue]:
    """Every active record with a non-null file_path must resolve under the
    project root (spec §4.2's own resolution rule). Read-only: uses
    init_paths.default_projects_root() directly rather than Plan B's
    resolve_project_root(), which would mkdir a missing project directory as
    a side effect (plan Decision 3) — inappropriate for a check that must
    never write. since=None (--full) checks every active row; otherwise only
    rows updated after since (plan Decision 5). Lists record groups via
    repository.list_record_groups(conn) directly against the connection this
    function was already handed, rather than service.schema_list(project=
    project) — the latter opens and closes a brand-new connection to the
    same project .db (its own WAL-pragma/busy-timeout setup included) purely
    to read the one list this function's own `conn` argument can already
    answer, an avoidable extra connection-open per run per project (spec
    G5: cost must never scale with unnecessary work)."""
    project_root = init_paths.default_projects_root() / project
    issues: list[VerifyIssue] = []
    for group in repository.list_record_groups(conn):
        record_group = group["record_group"]
        rows = repository.list_base_records(
            conn, record_group=record_group, since=since, until=None,
            limit=None, include_deleted=False,
        )
        for row in rows:
            file_path = row["file_path"]
            if file_path is None:
                continue
            if not (project_root / file_path).is_file():
                issues.append(VerifyIssue(
                    check="file-path-resolution", severity="FAIL",
                    record_group=record_group, record_id=row["id"],
                    message=(
                        f"record {row['id']}: file_path {file_path!r} does not "
                        f"resolve under {project_root}"
                    ),
                ))
    return issues
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_verify.py -v`
Expected: PASS (14 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/verify.py tests/pdata/test_verify.py
git commit -m "feat(pdata-verify): add file_path resolution check"
```

---

## Task 4: `verify.py` — suspicious-close-in-time double-update check

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/verify.py`
- Modify: `tests/pdata/test_verify.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_verify.py

def test_double_update_check_no_issue_on_first_sighting(monkeypatch, tmp_path):
    """A row verify has never seen before has nothing to compare against —
    it is recorded as a fresh watermark, not flagged."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(project="demo", record_group="key-events", content="x",
                                 file_path=None, fields={}, created_at=1000)
    conn = repository.connect("demo")
    try:
        verify.ensure_verify_tables(conn)
        with repository._immediate(conn):
            issues = verify.check_suspicious_double_updates(conn, "demo", since=None)
        assert issues == []
        watermark = conn.execute(
            "SELECT * FROM pdata_verify_watermark WHERE record_id=?", (record.id,),
        ).fetchone()
        assert watermark["last_seen_version"] == 1
    finally:
        conn.close()


def test_double_update_check_flags_two_updates_within_window(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(project="demo", record_group="key-events", content="x",
                                 file_path=None, fields={}, created_at=1000)
    conn = repository.connect("demo")
    try:
        verify.ensure_verify_tables(conn)
        with repository._immediate(conn):
            verify.check_suspicious_double_updates(conn, "demo", since=None)  # first sighting

        service.update_record(project="demo", record_id=record.id, expected_version=1,
                               content="v2", file_path=None, fields={}, updated_at=1010)
        service.update_record(project="demo", record_id=record.id, expected_version=2,
                               content="v3", file_path=None, fields={}, updated_at=1020)

        conn2 = repository.connect("demo")
        try:
            with repository._immediate(conn2):
                issues = verify.check_suspicious_double_updates(conn2, "demo", since=None)
            assert len(issues) == 1
            assert issues[0].severity == "WARN"
            assert issues[0].check == "suspicious-double-update"
            assert issues[0].record_id == record.id
        finally:
            conn2.close()
    finally:
        conn.close()


def test_double_update_check_ignores_single_update(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(project="demo", record_group="key-events", content="x",
                                 file_path=None, fields={}, created_at=1000)
    conn = repository.connect("demo")
    try:
        verify.ensure_verify_tables(conn)
        with repository._immediate(conn):
            verify.check_suspicious_double_updates(conn, "demo", since=None)
    finally:
        conn.close()

    service.update_record(project="demo", record_id=record.id, expected_version=1,
                           content="v2", file_path=None, fields={}, updated_at=1010)

    conn2 = repository.connect("demo")
    try:
        with repository._immediate(conn2):
            issues = verify.check_suspicious_double_updates(conn2, "demo", since=None)
        assert issues == []
    finally:
        conn2.close()


def test_double_update_check_ignores_updates_outside_window(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(project="demo", record_group="key-events", content="x",
                                 file_path=None, fields={}, created_at=1000)
    conn = repository.connect("demo")
    try:
        verify.ensure_verify_tables(conn)
        with repository._immediate(conn):
            verify.check_suspicious_double_updates(conn, "demo", since=None)
    finally:
        conn.close()

    far_future = 1000 + verify._DOUBLE_UPDATE_WINDOW_SECONDS + 1000
    service.update_record(project="demo", record_id=record.id, expected_version=1,
                           content="v2", file_path=None, fields={}, updated_at=1010)
    service.update_record(project="demo", record_id=record.id, expected_version=2,
                           content="v3", file_path=None, fields={}, updated_at=far_future)

    conn2 = repository.connect("demo")
    try:
        with repository._immediate(conn2):
            issues = verify.check_suspicious_double_updates(conn2, "demo", since=None)
        assert issues == []  # version advanced by 2, but not within the window
    finally:
        conn2.close()
```

Note: `service.update_record(..., updated_at=...)` is Plan A's own signature (Task 14/15 of Plan
A's plan) — it accepts an explicit `updated_at` override for the same reason `add_record` accepts
`created_at`, used here to make the "close in time" window deterministic in tests rather than
depending on wall-clock timing.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_verify.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'check_suspicious_double_updates'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/verify.py

def check_suspicious_double_updates(
    conn: sqlite3.Connection, project: str, *, since: int | None,
) -> list[VerifyIssue]:
    """Flag an active row whose version has advanced by >=2 and whose
    updated_at has moved by no more than _DOUBLE_UPDATE_WINDOW_SECONDS since
    the last time verify looked at it — i.e. at least two updates landed
    inside one polling interval (plan Decision 4). Always upserts every
    examined row's current (version, updated_at) into
    pdata_verify_watermark afterwards, so the next run's diff is against
    this run's state, regardless of whether an issue was raised this time.
    Caller must run this inside a transaction (repository._immediate) since
    it writes. Lists record groups via repository.list_record_groups(conn)
    directly against the already-open connection, not
    service.schema_list(project=project) — see check_file_path_resolution's
    docstring for why the latter is avoided here (an avoidable extra
    connection open per run per project, spec G5)."""
    issues: list[VerifyIssue] = []
    for group in repository.list_record_groups(conn):
        record_group = group["record_group"]
        rows = repository.list_base_records(
            conn, record_group=record_group, since=since, until=None,
            limit=None, include_deleted=False,
        )
        for row in rows:
            watermark = conn.execute(
                "SELECT last_seen_version, last_seen_updated_at "
                "FROM pdata_verify_watermark WHERE record_id=?",
                (row["id"],),
            ).fetchone()
            if watermark is not None:
                version_delta = row["version"] - watermark["last_seen_version"]
                time_delta = row["updated_at"] - watermark["last_seen_updated_at"]
                if version_delta >= 2 and time_delta <= _DOUBLE_UPDATE_WINDOW_SECONDS:
                    issues.append(VerifyIssue(
                        check="suspicious-double-update", severity="WARN",
                        record_group=record_group, record_id=row["id"],
                        message=(
                            f"record {row['id']} in {record_group!r}: version "
                            f"advanced by {version_delta} within {time_delta}s of "
                            f"the last verify pass — two updates landed unusually "
                            f"close together"
                        ),
                    ))
            conn.execute(
                "INSERT INTO pdata_verify_watermark "
                "(record_id, last_seen_version, last_seen_updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(record_id) DO UPDATE SET "
                "last_seen_version=excluded.last_seen_version, "
                "last_seen_updated_at=excluded.last_seen_updated_at",
                (row["id"], row["version"], row["updated_at"]),
            )
    return issues
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_verify.py -v`
Expected: PASS (18 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/verify.py tests/pdata/test_verify.py
git commit -m "feat(pdata-verify): add suspicious-close-in-time double-update check"
```

---

## Task 5: `verify.py` — `run_verify()` orchestration, `last_run()`, `discover_projects()`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/verify.py`
- Modify: `tests/pdata/test_verify.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/pdata/test_verify.py

def test_run_verify_persists_ok_summary_with_no_issues(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="ccst-ideas", content="an idea",
                        file_path=None, fields={}, created_at=1000)
    summary = verify.run_verify(project="demo", full=True)
    assert summary.status == "OK"
    assert summary.issues == []
    assert summary.project == "demo"
    assert summary.full_scan is True


def test_run_verify_persists_worst_status_across_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="filings", content="x",
                        file_path="missing.pdf", fields={}, created_at=1000)
    summary = verify.run_verify(project="demo", full=True)
    assert summary.status == "FAIL"  # file-path-resolution is FAIL-severity
    assert len(summary.issues) == 1


def test_run_verify_second_call_reads_persisted_last_run(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="notes", content="x",
                        file_path=None, fields={}, created_at=1)
    verify.run_verify(project="demo", full=True)
    last = verify.last_run("demo")
    assert last is not None
    assert last.status == "OK"


def test_run_verify_raises_for_project_with_no_existing_store(monkeypatch, tmp_path):
    """run_verify must never fabricate a brand-new, empty store (via repository.connect()'s
    own CREATE TABLE IF NOT EXISTS side effect) for a project name that has never had one —
    that would make `ccst pdata verify --project <typo>` silently report "clean" instead of
    surfacing the mistake. Matches discover_projects()'s own "only .dbs that already exist"
    standard (plan Decision 7) applied to a single named project too."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    with pytest.raises(ValueError, match="no data store"):
        verify.run_verify(project="never-touched-project", full=True)
    assert not (tmp_path / "never-touched-project.db").exists()


def test_last_run_returns_none_when_never_run(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    assert verify.last_run("never-verified-project") is None


def test_last_run_returns_none_when_db_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "does-not-exist"))
    assert verify.last_run("demo") is None


def test_discover_projects_lists_dbs_sorted(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.add_record(project="zeta", record_group="notes", content="x",
                        file_path=None, fields={}, created_at=1)
    service.add_record(project="alpha", record_group="notes", content="x",
                        file_path=None, fields={}, created_at=1)
    assert verify.discover_projects() == ["alpha", "zeta"]


def test_discover_projects_empty_when_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "does-not-exist"))
    assert verify.discover_projects() == []


def test_run_verify_prunes_old_runs_beyond_retention(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="notes", content="x",
                        file_path=None, fields={}, created_at=1)
    for _ in range(verify._MAX_RETAINED_RUNS + 5):
        verify.run_verify(project="demo", full=True)
    conn = repository.connect("demo")
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM pdata_verify_runs").fetchone()["c"]
        assert count == verify._MAX_RETAINED_RUNS
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_verify.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'run_verify'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/lib/pdata/verify.py

import json
import time

from cc_session_tools.lib.pdata import store  # add to existing import block


def _worst_status(issues: list[VerifyIssue]) -> str:
    if not issues:
        return "OK"
    return max((i.severity for i in issues), key=lambda s: _SEVERITY_ORDER[s])


def _last_run_at(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(run_at) AS m FROM pdata_verify_runs").fetchone()
    return row["m"] if row is not None and row["m"] is not None else None


def _persist_run(
    conn: sqlite3.Connection, *, run_at: int, full_scan: bool, status: str,
    issues: list[VerifyIssue],
) -> None:
    details = json.dumps([
        {"check": i.check, "severity": i.severity, "record_group": i.record_group,
         "record_id": i.record_id, "message": i.message}
        for i in issues
    ])
    conn.execute(
        "INSERT INTO pdata_verify_runs (run_at, full_scan, status, issue_count, details) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_at, int(full_scan), status, len(issues), details),
    )
    conn.execute(
        # Order by id, not run_at: run_at is second-resolution and two runs
        # in a tight loop (as in this plan's own retention test) can share a
        # value — id (the autoincrement rowid) is strictly monotonic with
        # insertion order regardless of clock resolution, so it is the
        # correct "most recent" tiebreak, here and in last_run() below.
        "DELETE FROM pdata_verify_runs WHERE id NOT IN ("
        "  SELECT id FROM pdata_verify_runs ORDER BY id DESC LIMIT ?"
        ")",
        (_MAX_RETAINED_RUNS,),
    )


def run_verify(*, project: str, full: bool = False) -> VerifySummary:
    """Run all three checks (spec §6.3) and persist the result. Row-count
    parity always runs in full (plan Decision 5); file_path resolution and
    the double-update check are scoped to rows changed since the last run
    unless full=True.

    Raises ValueError if `project` has no existing .db yet — repository.connect()
    would otherwise silently create one (its own CREATE TABLE IF NOT EXISTS side
    effect) and report a brand-new empty store as "clean", masking a typo'd or
    genuinely nonexistent project name behind a false-looking-fine result. This
    mirrors discover_projects()'s own "only .dbs that already exist" standard
    (plan Decision 7), applied here to a single explicitly-named project."""
    if not store.db_path(project).exists():
        raise ValueError(
            f"no data store found for project {project!r} — run 'ccst pdata add' "
            f"or 'ccst pdata init' for this project first"
        )
    conn = repository.connect(project)
    try:
        ensure_verify_tables(conn)
        since = None if full else _last_run_at(conn)

        issues: list[VerifyIssue] = []
        issues.extend(check_row_count_parity(conn, project))
        issues.extend(check_file_path_resolution(conn, project, since=since))

        run_at = int(time.time())
        with repository._immediate(conn):
            issues.extend(check_suspicious_double_updates(conn, project, since=since))
            status = _worst_status(issues)
            _persist_run(conn, run_at=run_at, full_scan=full, status=status, issues=issues)
        return VerifySummary(
            project=project, run_at=run_at, full_scan=full, status=status, issues=issues,
        )
    finally:
        conn.close()


def last_run(project: str) -> VerifySummary | None:
    """Read-only: the most recently persisted verify run, or None if verify
    has never run for this project (or its .db doesn't exist yet). Never
    triggers a run itself — ccst doctor calls this, not run_verify() (plan
    Decision 6)."""
    if not store.db_path(project).exists():
        return None
    conn = repository.connect(project)
    try:
        ensure_verify_tables(conn)
        row = conn.execute(
            "SELECT * FROM pdata_verify_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        issues = [VerifyIssue(**d) for d in json.loads(row["details"])]
        return VerifySummary(
            project=project, run_at=row["run_at"], full_scan=bool(row["full_scan"]),
            status=row["status"], issues=issues,
        )
    finally:
        conn.close()


def discover_projects() -> list[str]:
    """Every project with a .db under project_db_dir(), sorted (plan
    Decision 7) — used by both `ccst pdata verify --all-projects` and the doctor
    check, so a project need not have an on-disk folder yet to be found."""
    db_dir = store.project_db_dir()
    if not db_dir.is_dir():
        return []
    return sorted(p.stem for p in db_dir.glob("*.db"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_verify.py -v`
Expected: PASS (27 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/verify.py tests/pdata/test_verify.py
git commit -m "feat(pdata-verify): add run_verify orchestration, last_run, discover_projects"
```

---

## Task 6: CLI wiring — `ccst pdata verify`

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`
- Create: `tests/test_ccst_pdata_verify_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ccst_pdata_verify_cli.py
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path):
    env = os.environ.copy()
    env["CCST_PROJECT_DB_DIR"] = str(tmp_path / "project-db")
    env["CCST_PROJECTS_ROOT"] = str(tmp_path / "projects")
    return env


def test_pdata_verify_clean_project_exits_zero(base_env):
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "ccst-ideas",
         "--content", "an idea")
    r = _run(base_env, "pdata", "verify", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "demo: OK" in r.stdout


def test_pdata_verify_reports_issue_and_exits_one(base_env):
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "filings",
         "--content", "x", "--file", "missing.pdf")
    r = _run(base_env, "pdata", "verify", "--project", "demo")
    assert r.returncode == 1
    assert "FAIL" in r.stdout


def test_pdata_verify_all_with_no_projects_exits_two(base_env):
    r = _run(base_env, "pdata", "verify", "--all-projects")
    assert r.returncode == 2
    assert "no project" in r.stderr.lower()


def test_pdata_verify_all_iterates_every_project(base_env):
    _run(base_env, "pdata", "add", "--project", "alpha", "--group", "notes",
         "--content", "x")
    _run(base_env, "pdata", "add", "--project", "beta", "--group", "notes",
         "--content", "y")
    r = _run(base_env, "pdata", "verify", "--all-projects")
    assert r.returncode == 0, r.stderr
    assert "alpha: OK" in r.stdout
    assert "beta: OK" in r.stdout


def test_pdata_verify_requires_project_or_all(base_env):
    r = _run(base_env, "pdata", "verify")
    assert r.returncode == 2


def test_pdata_verify_project_not_found_exits_two(base_env):
    """A --project name with no existing .db (typo, or genuinely never touched) must be
    reported as an error, never silently created and reported clean (run_verify()'s own
    ValueError, plan Decision 8's "2 for a CLI/validation error")."""
    r = _run(base_env, "pdata", "verify", "--project", "never-touched-project")
    assert r.returncode == 2
    assert "no data store" in r.stderr.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ccst_pdata_verify_cli.py -v`
Expected: FAIL — argparse "invalid choice: 'verify'" (exit 2), `pdata` has no `verify` verb yet.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/cc_session_tools/cli/ccst.py — new section after Plan A's/Plan B's existing
# "---------- pdata ----------" handlers:

def _cmd_pdata_verify(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import verify

    projects = verify.discover_projects() if args.all_projects else [args.project]
    if not projects:
        print("ccst pdata verify: no project databases found", file=sys.stderr)
        return 2

    worst = 0
    for project in projects:
        try:
            summary = verify.run_verify(project=project, full=args.full)
        except ValueError as exc:
            print(f"ccst pdata verify: {project}: {exc}", file=sys.stderr)
            worst = max(worst, 2)
            continue
        print(f"{project}: {summary.status} ({len(summary.issues)} issue(s))")
        for issue in summary.issues:
            print(f"  [{issue.severity}] {issue.check}: {issue.message}")
        if summary.status != "OK":
            worst = max(worst, 1)
    return worst
```

```python
# add to pdata_sub in _build_parser(), after Plan A's/Plan B's existing pdata verbs:

    pdata_verify_parser = pdata_sub.add_parser(
        "verify", help="Run the integrity-check backstop (spec §6.3/§8.2)"
    )
    verify_target = pdata_verify_parser.add_mutually_exclusive_group(required=True)
    verify_target.add_argument("--project", metavar="NAME")
    verify_target.add_argument(
        "--all-projects", action="store_true",
        help="Verify every project with a .db under project-db/",
    )
    pdata_verify_parser.add_argument(
        "--full", action="store_true",
        help="Rescan every row instead of only rows changed since the last run",
    )
```

```python
# add to main() dispatch, inside the existing `if args.noun == "pdata":` block:

        if args.verb == "verify":
            sys.exit(_cmd_pdata_verify(args))
```

Also append one line to the module docstring's subcommand list (after the existing pdata/pdata
init lines from Plans A/B):

```
  pdata verify --project <name> | --all-projects   Run the integrity-check backstop (row-count
                                          parity, file_path resolution, suspicious
                                          double-updates) and persist the result for
                                          ccst doctor.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ccst_pdata_verify_cli.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_pdata_verify_cli.py
git commit -m "feat(pdata-verify): wire ccst pdata verify CLI verb"
```

---

## Task 7: `doctor.py` — `check_pdata_verify()` + wiring into `run_all_checks`/`_cmd_doctor`

**Files:**
- Modify: `src/cc_session_tools/lib/doctor.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/test_ccst_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_ccst_doctor.py

def test_check_pdata_verify_warns_when_never_run(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import Status, check_pdata_verify

    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    results = check_pdata_verify(["never-verified"])
    assert len(results) == 1
    assert results[0].status == Status.WARN
    assert results[0].name == "pdata-verify:never-verified"
    assert "not run yet" in results[0].reason


def test_check_pdata_verify_ok_when_last_run_clean(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import Status, check_pdata_verify
    from cc_session_tools.lib.pdata import init_paths, service, verify

    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="notes", content="x",
                        file_path=None, fields={}, created_at=1)
    verify.run_verify(project="demo", full=True)

    results = check_pdata_verify(["demo"])
    assert results[0].status == Status.OK


def test_check_pdata_verify_fails_when_last_run_had_a_fail_issue(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import Status, check_pdata_verify
    from cc_session_tools.lib.pdata import init_paths, service, verify

    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="filings", content="x",
                        file_path="missing.pdf", fields={}, created_at=1)
    verify.run_verify(project="demo", full=True)

    results = check_pdata_verify(["demo"])
    assert results[0].status == Status.FAIL
    assert "issue(s)" in results[0].reason


def test_check_pdata_verify_returns_one_result_per_project(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import check_pdata_verify

    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    results = check_pdata_verify(["alpha", "beta"])
    assert {r.name for r in results} == {"pdata-verify:alpha", "pdata-verify:beta"}
```

Also add a wiring-level test confirming `run_all_checks` includes these when a project list is
passed, and doesn't blow up when it's empty:

```python
# append to tests/test_ccst_doctor.py

def test_run_all_checks_includes_pdata_verify_when_projects_given(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import run_all_checks

    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "project-db"))
    # Isolates the ungated check_ccsched_job_registered loop Plan C wires into run_all_checks
    # (iterates every BUNDLED_CCSCHED_JOBS entry, including this plan's own "pdata-verify-all"
    # once Task 8 lands) — without this, registry.load_registry() resolves scheduler.store.connect()
    # to the real production ccsched.db (CC_SCHEDULER_DIR, else CCST_DATA_HOME/paths.data_home())
    # and creates its schema there via CREATE TABLE IF NOT EXISTS. Matches Plan C's own
    # test_run_all_checks_includes_bundled_ccsched_job_checks isolation pattern.
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
        pdata_verify_projects=["demo"],
    )
    assert any(r.name == "pdata-verify:demo" for r in results)


def test_run_all_checks_skips_pdata_verify_when_projects_none(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import run_all_checks

    # Same isolation as the test above — run_all_checks() unconditionally exercises Plan C's
    # check_ccsched_job_registered loop regardless of pdata_verify_projects.
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
        pdata_verify_projects=None,
    )
    assert not any(r.name.startswith("pdata-verify:") for r in results)
```

Note: this matches the existing `test_run_all_checks_returns_list`/`test_run_all_checks_includes_
hook_checks` fixture pattern already in this file (real bundle path, a minimal written
`settings.json`, and the two `CLAUDE_SESSION_TOOLS_*` env keys explicitly set to `None`) rather
than inventing a new untested minimal-args shape — `Path` must already be imported at the top of
`tests/test_ccst_doctor.py` for this to work; add the import if this specific test file doesn't
already have it.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: FAIL with `ImportError: cannot import name 'check_pdata_verify'`, and
`TypeError: run_all_checks() got an unexpected keyword argument 'pdata_verify_projects'`

- [ ] **Step 3: Write minimal implementation**

```python
# add "import time" to src/cc_session_tools/lib/doctor.py's existing top-of-file import
# block (alongside importlib.metadata, json, os, shutil, sqlite3, subprocess — doctor.py
# does not import the stdlib time module yet).
```

```python
# add to src/cc_session_tools/lib/doctor.py, near check_pending_data_store_migration:

def _fmt_epoch(epoch: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def check_pdata_verify(projects: list[str]) -> list[CheckResult]:
    """Read (never run) each project's most recently persisted `ccst pdata
    verify` result — a background ccsched job produces it, this only
    reports it (spec §8.2's own "feeding failures into ccst doctor... rather
    than only running silently in the background"). A project with no
    verify db yet, or that has never had a verify run, WARNs rather than
    FAILing — matching check_pending_data_store_migration's own precedent
    that an unremarkable not-yet-run state should never read as broken.

    Deliberately takes validated project *names*, not pre-resolved Path objects
    like check_data_stores()'s `store_paths` — unlike a generic file-backed
    store, a project's pdata store is never addressed by raw path anywhere in
    this codebase (service.py, repository.py, verify.py itself are all keyed by
    project name; repository.connect(project) does the one env-var-aware path
    lookup, in one place). Accepting a Path here would mean either duplicating
    that lookup a second time in ccst.py just to hand doctor.py something it
    then discards in favour of calling verify.last_run(project) anyway, or
    threading a parallel path-based entry point through verify.py that nothing
    else needs. The project name *is* this check's resolved handle, in the same
    sense that store_paths' Path values are check_data_stores()'s."""
    from cc_session_tools.lib.pdata import verify

    results: list[CheckResult] = []
    for project in projects:
        name = f"pdata-verify:{project}"
        summary = verify.last_run(project)
        if summary is None:
            results.append(CheckResult(
                name=name, status=Status.WARN,
                reason="ccst pdata verify has not run yet for this project",
            ))
            continue
        if summary.status == "OK":
            results.append(CheckResult(
                name=name, status=Status.OK,
                reason=f"last run {_fmt_epoch(summary.run_at)}, no issues",
            ))
        else:
            status = Status.FAIL if summary.status == "FAIL" else Status.WARN
            results.append(CheckResult(
                name=name, status=status,
                reason=(
                    f"last run {_fmt_epoch(summary.run_at)}: {len(summary.issues)} "
                    f"issue(s), worst={summary.status} — run 'ccst pdata verify "
                    f"--project {project}' for details"
                ),
            ))
    return results
```

```python
# modify run_all_checks()'s signature in src/cc_session_tools/lib/doctor.py — adding
# pdata_verify_projects alongside Plan B's own projects_root parameter (Plan B Task 12), not
# in place of it: this plan's check_pdata_verify() and Plan B's check_pending_pdata_migration()
# are two distinct checks that both key off "the set of pdata projects" but take different-shaped
# arguments (a list of project names vs. the projects root directory) and both must keep firing.

def run_all_checks(
    *,
    installed_version: str,
    settings_path: Path,
    bundle_path: Path,
    skills_source_dir: Path | None,
    skills_target_dir: Path,
    env: dict[str, str | None],
    skip_pypi: bool = False,
    store_paths: dict[str, Path] | None = None,
    legacy_migration_paths: LegacyMigrationPaths | None = None,
    projects_root: Path | None = None,
    pdata_verify_projects: list[str] | None = None,
) -> list[CheckResult]:
```

Add the docstring's parameter entry and, near the end of the function body (after the existing
`check_pending_data_store_migration` call), add:

```python
    if pdata_verify_projects is not None:
        results.extend(check_pdata_verify(pdata_verify_projects))
```

```python
# modify src/cc_session_tools/cli/ccst.py's _cmd_doctor(): add the import and pass-through.
# Plan B's Task 12 already added the `from cc_session_tools.lib.pdata.init_paths import
# default_projects_root` import and the `projects_root=default_projects_root()` keyword to this
# same run_all_checks(...) call — that argument stays; this plan adds one more keyword alongside
# it, it does not replace it.

    from cc_session_tools.lib.pdata import verify as _pdata_verify  # new import

    # ... existing body unchanged up to the run_all_checks(...) call, then:

    results = run_all_checks(
        installed_version=__version__,
        settings_path=settings_path,
        bundle_path=bundle_path,
        skills_source_dir=skills_source_dir,
        skills_target_dir=skills_target_dir,
        env=env_vars,
        skip_pypi=args.no_pypi,
        store_paths=store_paths,
        legacy_migration_paths=legacy_migration_paths,
        projects_root=default_projects_root(),
        pdata_verify_projects=_pdata_verify.discover_projects(),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/doctor.py src/cc_session_tools/cli/ccst.py tests/test_ccst_doctor.py
git commit -m "feat(pdata-verify): add ccst doctor pdata-verify:<project> check"
```

---

## Task 8: Register `pdata-verify-all` in the shared `BUNDLED_CCSCHED_JOBS` registry

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/bundled_jobs.py` (Plan C's Task 7 file)
- Modify: `tests/test_scheduler_bundled_jobs.py` (Plan C's Task 7 file)

Per Plan C's own "Post-plan note for whoever picks up Plan D"
(2026-07-30-ccst-pm-update-central-files.md, end of file) — **a binding constraint on this plan,
not a suggestion**: `lib/scheduler/bundled_jobs.py`'s `BUNDLED_CCSCHED_JOBS` tuple (Plan C Task 7)
is the single place a new install-time `ccsched` job gets declared. This task adds one more
`BundledJob` entry to that same tuple — it does **not** create a `verify_job.py` module, does
**not** call `registry.add_job()` directly, does **not** add a new step to
`_cmd_install_everything`'s `_INSTALL_STEPS`/`steps`/`dispatch` tables (Plan C's own "Scheduled
jobs" step, added by its Task 8, already provisions every entry in `BUNDLED_CCSCHED_JOBS`), and
does **not** add a new `ccst doctor` check for job registration (Plan C's Task 9
`check_ccsched_job_registered(job_id)` is already generic over any `job_id` in the list). This
plan's own `check_pdata_verify()` (Task 7, above) is unaffected and unrelated — it reports the
*content* of verify's last persisted run, not whether the `ccsched` job itself is registered.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_scheduler_bundled_jobs.py (Plan C's file — do not create a new file)

def test_bundled_jobs_contains_pdata_verify_all_job():
    ids = [job.job_id for job in bundled_jobs.BUNDLED_CCSCHED_JOBS]
    assert "pdata-verify-all" in ids


def test_pdata_verify_all_job_command_and_cadence():
    job = next(
        j for j in bundled_jobs.BUNDLED_CCSCHED_JOBS if j.job_id == "pdata-verify-all"
    )
    assert job.command == ("ccst", "pdata", "verify", "--all-projects")
    assert job.cadence == "daily@03:00"  # plan Decision 9 — avoids interactive-session hours
    assert job.coalesce == "one"
    assert job.surface is False  # results reach ccst doctor, not a direct interrupt (spec §8.2)
```

Note: Plan C's own `test_bundled_job_ids_are_unique` (already in this file) continues to pass
unchanged — it iterates whatever is in `BUNDLED_CCSCHED_JOBS`, now two entries instead of one, and
still asserts no duplicate `job_id`s.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scheduler_bundled_jobs.py -v`
Expected: FAIL — `StopIteration` from the `next(...)` call (no `pdata-verify-all` entry exists yet
in `BUNDLED_CCSCHED_JOBS`).

- [ ] **Step 3: Write minimal implementation**

```python
# modify src/cc_session_tools/lib/scheduler/bundled_jobs.py — append one BundledJob to the
# existing BUNDLED_CCSCHED_JOBS tuple; Plan C's own pm-session-output-reconcile entry and the
# BundledJob dataclass itself are unchanged:

BUNDLED_CCSCHED_JOBS: tuple[BundledJob, ...] = (
    BundledJob(
        job_id="pm-session-output-reconcile",
        cadence="every:7d",
        coalesce="one",
        catchup_window="7d",
        timeout="300s",
        surface=True,
        command=("ccst", "pdata", "reconcile-session-output", "--all-projects"),
    ),
    BundledJob(
        job_id="pdata-verify-all",
        cadence="daily@03:00",
        coalesce="one",
        catchup_window="7d",
        timeout="300s",
        surface=False,
        command=("ccst", "pdata", "verify", "--all-projects"),
    ),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scheduler_bundled_jobs.py -v`
Expected: PASS (5 tests total in the file — Plan C's original 3 plus this task's 2)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/bundled_jobs.py tests/test_scheduler_bundled_jobs.py
git commit -m "feat(pdata-verify): register pdata-verify-all in the shared bundled-ccsched-jobs list"
```

No further wiring is part of this plan: `ccst install-everything` already provisions
`pdata-verify-all` the moment it appears in `BUNDLED_CCSCHED_JOBS` (Plan C's "Scheduled jobs"
step), and `ccst doctor` already health-checks its registration state the same way (Plan C's
`check_ccsched_job_registered`, called once per entry in the list).

---

## Task 9: `skills/pm-pdata-schema-design/SKILL.md`

**Files:**
- Create: `skills/pm-pdata-schema-design/SKILL.md`

This task has no automated test — a `SKILL.md` is a judgement document, not executable code (same
as Plan B's `pm-project-init`). Verification is a manual re-read against spec §8.1's description
before committing.

- [ ] **Step 1: Write the skill**

```markdown
---
name: pm-pdata-schema-design
description: Use before writing a genuinely new kind of structured data into any project's ccst pdata store - deciding whether it fits an existing record_group, needs a brand-new group, and whether that group needs an extension table or free-text content suffices. Triggers on "I need to track a new kind of record", "what record_group should this be", "does this need an extension table", "add a new field to ccst pdata", or any session about to call `ccst pdata add`/`schema add-field` for a record shape it hasn't used before. Prevents lazy catch-all groups (`notes`/`misc`) that silently recreate the EAV undifferentiated-bucket problem the schema deliberately rejected.
---

# ccst pdata schema design

`ccst pdata` (spec `2026-07-26-per-project-data-store-spec.md` §4-§5) gives every project one
SQLite store with a fixed base `records` table plus optional per-`record_group` extension tables.
The schema itself never needs a CCST source change (G8) - the judgement call this skill exists for
is entirely about **how to use it well** for a new kind of data, not how to extend the tool.

## Before doing anything: check what already exists

```sh
ccst pdata schema list --project <name>
```

This lists every `record_group` already in the project's store, its row count, whether it has an
extension table, and when it was last updated. **Always run this before inventing a new group.** A
new "idea" for a group that's actually just `ccst-ideas` again, spelled differently, is the exact
failure this skill exists to prevent.

If a plausible existing group turns up, run `ccst pdata schema show --project <name> --group
<group>` to see its extension columns (with descriptions) before deciding whether the new data
fits there as-is, fits with one new field (`schema add-field`), or is different enough to warrant
its own group.

## Decision 1: does this fit an existing `record_group`?

Fits if the new items are the same *kind* of thing an existing group already holds - same rough
shape of content, same audience, same lifecycle (append-only log vs. edit-in-place current-state
vs. stacked snapshot - spec §4.3). A few new structured fields on otherwise-similar content is not
a reason for a new group; add the field instead (`ccst pdata schema add-field`).

Does **not** fit merely because two things are both "notes" or both "logs" in a generic sense -
that vagueness is exactly how a catch-all group forms. Ask: if this project's next session needed
to find these items again, would they look in the existing group's name, or would that name
mislead them?

## Decision 2: if not, what should the new group be called?

- Follow the naming convention exactly: lowercase letters, digits, hyphens only,
  `^[a-z0-9]+(-[a-z0-9]+)*$` (spec §4.2) - `ccst pdata` rejects anything else at the CLI boundary,
  so get it right the first time rather than discovering the rejection mid-migration.
- Name it for what the content **is**, specifically enough that a future session doesn't need to
  open it to know what's inside - `key-events`, `filings`, `session-output`, not `data`/`stuff`/
  `misc`/`notes`. If the best name you can find is a generic bucket word, that's a signal to look
  harder for what actually distinguishes these records, not a signal to accept the generic name.
- One content-modelling shape per group (spec §4.3): append-only log, edit-in-place current-state,
  or stacked dated snapshots. Mixing shapes in one group (some rows are a growing log, others get
  edited repeatedly) makes `update`/`list --since` behave inconsistently for callers who don't know
  which rows are which - split into two groups instead if a genuine mix shows up.

## Decision 3: does it need an extension table, or does free-text `content` suffice?

Needs an extension table (`ccst pdata schema add-field --project <name> --group <group> --field
<name>:<TYPE> [--description "..."]`) when a caller will realistically need to **query or filter**
on a specific field later - `WHERE sender = ?`, `WHERE due_date < ?`, `WHERE is_read = 0`. That's
what real typed/indexed columns are for (spec §4.3's rejection of a generic EAV table applies here
too: don't reinvent EAV by cramming structured data into `content` as a serialized blob just to
avoid a schema call).

Free-text `content` suffices when the data is genuinely prose - a decision's rationale, a
correspondence transcription, a plan document's body. Don't add a field "just in case it's useful
to filter on later" - that's speculative schema, and `schema add-field` is cheap enough to run
later, the moment an actual query need shows up (idempotent, no migration ceremony: `ALTER TABLE
ADD COLUMN`, defaults to `NULL` for existing rows, spec §4.3).

Always give `--description` when adding a field with a non-obvious meaning - it is the only home
for that information (spec §4.4: `record_group_fields` stores no type information, only prose, so
a field with a blank description is genuinely undocumented for the next session that runs `schema
show`).

## Quick reference

| Question | Where the answer comes from |
|---|---|
| What groups exist already? | `ccst pdata schema list --project <name>` |
| What fields does a group have? | `ccst pdata schema show --project <name> --group <group>` |
| Add a field to an existing group | `ccst pdata schema add-field --project <name> --group <group> --field <name>:<TYPE> --description "..."` |
| Which content-modelling shape? | append-only log / edit-in-place current-state / stacked dated snapshots (spec §4.3) |
```

- [ ] **Step 2: Verify the skill installs cleanly**

Run: `cd /home/chris/repos/claude-code-session-tools/.worktrees/pdata-core && uv run python -m cc_session_tools.cli.ccst skills install --source skills --target /tmp/ccst-skill-check-$$ ; ls /tmp/ccst-skill-check-$$ 2>/dev/null | grep pm-pdata-schema-design; rm -rf /tmp/ccst-skill-check-$$`
Expected: `_discover_skills()` finds the new directory (it has a `SKILL.md`) and the dry-run table
lists it with action `create` — confirms the frontmatter and file placement are well-formed
without needing a dedicated automated test for a documentation file.

- [ ] **Step 3: Commit**

```bash
git add skills/pm-pdata-schema-design/SKILL.md
git commit -m "docs(pdata): add pm-pdata-schema-design skill"
```

---

## Task 10: `skills/pm-pdata-conflict-resolution/SKILL.md`

**Files:**
- Create: `skills/pm-pdata-conflict-resolution/SKILL.md`

- [ ] **Step 1: Write the skill**

```markdown
---
name: pm-pdata-conflict-resolution
description: Use immediately when `ccst pdata update` or `ccst pdata delete` exits 3 (a version conflict) - takes the CLI's current-vs-attempted diff and presents it to Chris for reconciliation, per spec §6.2's "the session asks Chris how to reconcile" contract. Triggers on exit code 3 from either command, or phrasing like "pdata update conflict", "version conflict on a record", "someone else already updated this row". Never auto-retries, never silently picks a side, never discards either version.
---

# ccst pdata conflict resolution

`ccst pdata update`/`delete` use optimistic concurrency (spec §6.2): the caller passes the
`--version` it last read, and the write only succeeds if that version still matches what's in the
store. A non-zero-rows-affected result means another session's write landed first. The CLI detects
this, exits `3`, and prints the current row's diff against what this session tried to write - this
skill is what happens next.

**Never auto-retry, never auto-merge, never silently keep one side and discard the other.** The
spec is explicit that this always surfaces to Chris - the layered defense here is structural
avoidance -> optimistic concurrency -> **a human decides**, not an increasingly clever automatic
resolution attempt (spec §6, point 2: "auto-retry or silent-log-and-skip were both rejected in
favour of always surfacing to Chris").

## When this triggers

Immediately when a `ccst pdata update`/`delete` call in the current session exits `3`. Do not wait
to be asked - a version conflict means data is at stake and the calling session should not just
move on to its next step with the write silently having failed.

## What to do

1. **Read the printed diff as-is.** The CLI already printed the current row (what's actually in
   the store now, i.e. the winning write) against the attempted change (what this session tried to
   write) - re-run with `--format json` if a structured diff is more useful than the table for the
   specific fields involved:

   ```sh
   ccst pdata get --project <name> --id <id>  # confirm current state directly if useful
   ```

2. **Present both sides to Chris plainly** - what's currently stored, what this session tried to
   change it to, and (if knowable from context - e.g. two different session transcripts, two
   different times of day) what likely produced each version. Do not editorialise about which
   version is "probably right" - that judgement belongs to Chris, who has context this session
   doesn't (which session was more authoritative, which change was more recent in wall-clock intent
   rather than just `updated_at`).

3. **Ask Chris explicitly how to reconcile.** Typical resolutions, none of them automatic:
   - Keep the current (winning) version as-is; this session's attempted change is dropped.
   - Re-apply this session's change on top of the current version - re-read the current row to get
     its fresh `version`, then re-run `ccst pdata update --version <fresh-version> ...` with
     (possibly hand-merged) content.
   - Merge specific fields from both versions into one new `update` call.
   - If the conflict reveals a genuine double-update problem (the same logical fact edited by two
     concurrent sessions because both were unaware of the other), that's worth a note back to
     whichever process let two sessions touch the same project store unknowingly - not something
     this skill fixes on its own.

4. **Never touch `--version` speculatively.** Guessing a version number to force a write through
   defeats the entire mechanism (spec §6.2) and risks silently overwriting whatever the other
   session wrote. Always re-`get` the row to learn its real current version before writing again.

## Relationship to `ccst pdata verify`'s double-update check

`ccst pdata verify`'s suspicious-close-in-time double-update check (spec §6.3) is a *different*
mechanism catching a *different* case: two updates that both succeeded in sequence (no conflict
ever raised, because each update's `--version` matched at the moment it ran) but landed suspiciously
close together in time. That surfaces as a WARN in `ccst doctor`, not as an exit-3 conflict from
`update`/`delete` - if that's what brought you here, re-read `ccst pdata verify`'s own output
instead; there is no "current vs. attempted" diff for that case, since both writes structurally
succeeded. This skill is specifically for the exit-3 case.
```

- [ ] **Step 2: Verify the skill installs cleanly**

Run: `cd /home/chris/repos/claude-code-session-tools/.worktrees/pdata-core && uv run python -m cc_session_tools.cli.ccst skills install --source skills --target /tmp/ccst-skill-check-$$ ; ls /tmp/ccst-skill-check-$$ 2>/dev/null | grep pm-pdata-conflict-resolution; rm -rf /tmp/ccst-skill-check-$$`
Expected: found, action `create`.

- [ ] **Step 3: Commit**

```bash
git add skills/pm-pdata-conflict-resolution/SKILL.md
git commit -m "docs(pdata): add pm-pdata-conflict-resolution skill"
```

---

## Task 11: Full suite + mypy strict + version bump + CHANGELOG

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS, 0 failures — confirms no cross-task regressions (e.g. `ccst.py`'s module
docstring update, `doctor.py`'s `run_all_checks` signature change, or Plan C's
`bundled_jobs.py`/`test_scheduler_bundled_jobs.py` accidentally breaking an existing caller/test).

- [ ] **Step 2: Run `mypy --strict` on the new/modified code**

Run: `uv run mypy --strict src/cc_session_tools/lib/pdata/verify.py src/cc_session_tools/lib/scheduler/bundled_jobs.py src/cc_session_tools/lib/doctor.py src/cc_session_tools/cli/ccst.py`
Expected: no errors (per this repo's `CLAUDE.md`, `mypy --strict` is a build-failure gate — fix
any type errors surfaced here with precise types, never a cast or `Any`).

- [ ] **Step 3: Bump the version (minor)**

Read the current `version` in `pyproject.toml` at the time this task actually runs (do not assume
a specific predecessor number — see the Versioning section above) and bump its minor component by
one. This plan lands only after **both** Plan B and Plan C have landed (Versioning section above),
and both of those plans' own Task 11/Task 14 unconditionally perform their own minor bump — so by
the time this task runs, two prior bumps have already happened: Plan A → `1.1.0`, then whichever of
Plan B/Plan C landed second → `1.3.0` (the other having already bumped it to `1.2.0` first). E.g. if
the file currently reads `1.3.0`, this task sets it to `1.4.0`.

```toml
# pyproject.toml
[project]
name = "cc-session-tools"
version = "<current-minor + 1>"
```

- [ ] **Step 4: Add the CHANGELOG entry**

```markdown
# CHANGELOG.md — insert under "## [Unreleased]" (or the section covering this repo's current
# release-cut state at the time this task actually runs — follow whatever convention the other
# entries in the file are using then).

### Added

- **`ccst pdata verify` — the integrity-check backstop.** `--project <name> [--full] | --all-projects`
  runs three checks per project: row-count parity against still-archived migration originals,
  `file_path` resolution, and suspiciously-close-in-time double-updates (spec §6.3) — results are
  persisted so `ccst doctor` can report a `pdata-verify:<project>` check cheaply, without doctor
  itself paying the cost of a verify pass. A `pdata-verify-all` `ccsched` job (daily@03:00) is
  provisioned automatically by `ccst install-everything`, feeding doctor rather than paging
  anyone directly.
- **`pm-pdata-schema-design` and `pm-pdata-conflict-resolution` skills.** The first is invoked
  before writing a genuinely new kind of structured data into a project's `ccst pdata` store
  (existing group vs. new group vs. extension table vs. free-text content); the second is invoked
  whenever `ccst pdata update`/`delete` exits 3 (a version conflict), presenting the current-vs-
  attempted diff for reconciliation rather than auto-retrying or silently picking a side.

Note: `ccst pdata export` (spec §5's remaining `pdata` subcommand) is not designed or implemented
by this plan, nor by any prior `pdata` plan — flagged as the concrete scope for a future Plan E.
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(pdata): bump version for ccst pdata verify + scheduling + skills"
```

---

## Post-plan note: the one remaining gap in the `pdata` subcommand surface

Spec §5 names three "Project lifecycle" subcommands: `init`, `verify`, and `export`. Plan B covers
`init`, this plan covers `verify`. **`ccst pdata export` has no plan behind it yet** — not this
one, not A, not B, not (per the dispatching instructions for this plan) C. Whoever picks up a
future Plan E should start from spec §5's own one-line description ("Produces the 'courtesy copy'
export... Never the source of truth — read-only, point-in-time... for when a flat-file copy is
genuinely useful") and `--format csv|md`/`--out <path>` as the only spec'd flags.
