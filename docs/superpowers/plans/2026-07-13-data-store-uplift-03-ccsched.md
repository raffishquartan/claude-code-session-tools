# Phase 3: `ccsched` → `ccsched.db` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Read `2026-07-13-data-store-uplift-00-overview.md` (cross-phase env-var/DB-layout/migration-safety decisions) and `2026-07-13-data-store-uplift-01-shared-infra.md` (the `lib/db.py` / `lib/paths.py` contract this phase consumes) **before** starting. This phase depends on Phase 1 being merged. Also read `docs/superpowers/specs/2026-06-22-scheduled-tasks-catchup-design.md` — its §N references appear throughout the code's docstrings and must be preserved.

**Goal:** move the `ccsched` scheduler's four flat-file/TOML/JSON stores (`jobs.toml`, `state.json`, `.cursors/<uuid>.json`, `.reconcile.<uuid>.ts`) onto a single WAL-mode `ccsched.db`, eliminating the whole-file read-modify-write races (R1, R2, R4) and the one non-atomic write (the reconcile throttle), while leaving the file-based in-flight lock and the `ccsched` CLI contract byte-for-byte unchanged.

**Architecture:** every scheduler store module (`registry`, `state`, `cursor`, plus a new `throttle`) is rewired to open `ccsched.db` through a new `lib/scheduler/store.py` helper (thin wrapper over Phase 1's `db.connect`). Whole-file rewrites become targeted single-row `INSERT`/`UPDATE`/`DELETE`/`UPSERT` statements; the single read-then-write path (failure counting) uses `BEGIN IMMEDIATE`. The in-flight lock (`.run.<job-id>.lock`, `O_CREAT|O_EXCL`) stays exactly as-is — file-based, untouched — because it is the sole no-duplicate-execution guarantee and has no single-primitive SQLite equivalent (§10). A one-shot `ccst migrate ccsched` script performs a non-destructive write-verify-backup-delete migration of pre-existing data.

**Tech Stack:** Python 3.11 stdlib (`sqlite3`, `pathlib`, `os`, `json`, `threading`), the Phase 1 `cc_session_tools.lib.db` / `cc_session_tools.lib.paths` modules, pytest with `monkeypatch.setenv("CC_SCHEDULER_DIR", ...)`, real-subprocess CLI tests, multi-thread race tests.

---

## Prerequisites (do not start until true)

- [ ] Phase 1 is merged: `cc_session_tools.lib.db.connect(path, *, ddl, readonly)` and `cc_session_tools.lib.paths.data_home()` exist and pass their tests.
- [ ] You are on the `f/claude-data-store-uplift` branch (or a worktree of it), synced with `main`.
- [ ] `uv sync --extra dev` has been run in this worktree.

Run `uv run pytest tests/scheduler -q` once now to confirm the existing scheduler suite is green before you change anything. If it is not green, stop and fix that first — you own the branch while you are in it.

---

## Fixed contract (binding — do not redecide)

- **Env var:** `CC_SCHEDULER_DIR` (kept). Now a directory holding `ccsched.db` **and** the `.run.<job-id>.lock` files. **Its default changes** from `~/.claude/cc-scheduler` to `cc_session_tools.lib.paths.data_home()` (overview §1/§2). This is a deliberate path move for the whole subsystem, not just a format change — the migration script (Task 10) handles relocating pre-existing data.
- **Connection helper:** every DB access goes through `cc_session_tools.lib.db.connect(...)` (Phase 1). Never hand-roll pragma setup.
- **CLI unchanged:** `ccsched add/edit/list/enable/disable/remove/run/status/sweep/_run-job` keep identical arguments, stdout/stderr text, and exit codes. The existing `tests/scheduler/test_ccsched_cli.py` (black-box subprocess assertions) must keep passing with **zero edits** except where a test asserts on the *storage artefact itself* (e.g. `state.json` contents in `test_run_job_worker_executes_and_records`), which is rewritten to assert on the DB.
- **Ledger stays flat-file this phase.** The catch-up ledger is `fires.jsonl` (telemetry). It moves to `telemetry.db` in **Phase 5**, not here. Therefore the cursor's stored value keeps its exact current meaning — *a count of `hook=="catchup"` rows already surfaced* — and `ledger.py` is **not touched** in this phase. Only where the cursor value is *stored* changes (file → DB row).
- **Lock stays separate.** `lib/scheduler/lock.py` is **not modified**. The `state.in_flight` column is retained purely as reconcile's fast-path skip hint (`reconcile.py` reads it), never as a correctness guarantee.

---

## Schema design (`ccsched.db`)

Four tables, all created `IF NOT EXISTS` by `store.py`'s `_DDL` on first connect (overview §8 — "each script creates its schema on first access").

```sql
-- Registry (was jobs.toml). Row insertion order == registry order (reconcile
-- iterates in registry order and applies its launch cap in that order; `list`
-- prints in that order). SQLite's implicit rowid increments with INSERT, so
-- `ORDER BY rowid` reproduces jobs.toml's file order. UPDATEs (edit/enable)
-- keep the rowid, so order is stable across edits; a remove+re-add moves the
-- id to the end, exactly as the old load→mutate→rewrite did.
CREATE TABLE IF NOT EXISTS jobs (
    job_id         TEXT PRIMARY KEY,      -- unique kebab id; duplicate-insert -> IntegrityError (R1)
    cadence        TEXT NOT NULL,
    coalesce_kind  TEXT NOT NULL CHECK (coalesce_kind IN ('one', 'each')),
    command        TEXT NOT NULL,         -- JSON array of argv strings
    surface        INTEGER NOT NULL,      -- 0/1
    enabled        INTEGER NOT NULL,      -- 0/1
    catchup_window TEXT NOT NULL,
    timeout        TEXT NOT NULL
);

-- Per-job run state (was state.json). No FK to jobs: removing a job does NOT
-- delete its state today (remove_job only rewrites jobs.toml), so state may
-- legitimately outlive its registry row. Preserve that — do not add a
-- cascading FK.
CREATE TABLE IF NOT EXISTS job_state (
    job_id               TEXT PRIMARY KEY,
    registered_at        TEXT NOT NULL,
    last_success         TEXT,                       -- nullable
    last_attempt         TEXT,                       -- nullable
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    suspended            INTEGER NOT NULL DEFAULT 0,  -- 0/1
    in_flight_pid        INTEGER,                    -- in_flight present iff pid IS NOT NULL
    in_flight_started_at TEXT,
    in_flight_instants   INTEGER
);

-- Per-session surfacing cursor (was .cursors/<uuid>.json). offset = count of
-- catchup ledger rows already surfaced to this session (unchanged meaning; the
-- ledger is still fires.jsonl until Phase 5).
CREATE TABLE IF NOT EXISTS cursors (
    session_uuid TEXT PRIMARY KEY,
    offset       INTEGER NOT NULL
);

-- Per-session reconcile throttle (was .reconcile.<uuid>.ts). Kept SEPARATE from
-- cursors (see decision note below).
CREATE TABLE IF NOT EXISTS reconcile_throttle (
    session_uuid       TEXT PRIMARY KEY,
    last_reconciled_at TEXT NOT NULL
);
```

**Decision — `cursors` and `reconcile_throttle` kept separate (brief left this to us):** they are both keyed on `session_uuid` but have distinct lifecycles and seed points — the cursor is seeded on the *first hook call of a session* (at the current ledger end), the throttle is stamped *after each reconcile*. A combined table would need nullable columns and per-field UPSERT with awkward "which field is authoritative" semantics. Two single-column-payload tables give clean, invariant-free UPSERTs (matching the brief's note that neither needs `BEGIN IMMEDIATE`) and mirror the existing `ccst gc report` split, which already treats `scheduler-reconcile-markers` and `scheduler-cursors` as two separate stores (`session_gc.py`).

**Decision — `command` stored as JSON text:** argv is a `tuple[str, ...]`; JSON round-trips it losslessly and matches how `cccs_hooks/cache.py` already stores list data (`heuristic_names TEXT` = `json.dumps(...)`). Reconstructed via `tuple(json.loads(row["command"]))` on load.

**Decision — no re-validation on load:** writes go through `jobspec.validate_job_fields` at the CLI boundary (unchanged). Rows in the DB are therefore already valid, so `load_registry()` constructs `JobSpec` directly from rows without re-validating — per the "validate at boundaries, trust internals" standard. (The old TOML loader re-validated because `jobs.toml` was hand-editable; the DB is not.)

**Transaction discipline:**
- Single-statement writes (`set_in_flight`, `clear_in_flight`, `clear_suspended`, `record_success`, cursor/throttle UPSERT, registry INSERT/UPDATE/DELETE) auto-acquire the write lock; Phase 1's `busy_timeout=5000` handles contention. No explicit `BEGIN` needed.
- The **one** read-then-write path — `record_failure` (read `consecutive_failures`/`suspended`, compute via `next_failure_count`, write back, report `newly_suspended`) — uses `BEGIN IMMEDIATE`. Rationale: two concurrent deferred transactions would each start as readers, then both try to upgrade to writer, and one gets an immediate `SQLITE_BUSY` that `busy_timeout` cannot resolve (it is a deadlock, not mere contention). `BEGIN IMMEDIATE` takes the write lock up front, so a second writer blocks-and-retries within the busy timeout instead of failing. This is the correctness core of R2.

---

## File Structure

**Create:**
- `src/cc_session_tools/lib/scheduler/store.py` — DB path resolution, `scheduler_dir()` (moved here, new default), `connect()`, and the `_DDL`.
- `src/cc_session_tools/lib/scheduler/throttle.py` — reconcile-throttle read/write (was inline in `catchup.py`).
- `src/cc_session_tools/cli/migrate_ccsched.py` — one-shot non-destructive migration, exposed as `ccst migrate ccsched`.
- `tests/scheduler/test_store.py`
- `tests/scheduler/test_throttle.py`
- `tests/test_migrate_ccsched.py`

**Modify:**
- `src/cc_session_tools/lib/scheduler/state.py` — replace JSON I/O with DB ops; keep pure helpers.
- `src/cc_session_tools/lib/scheduler/registry.py` — replace TOML I/O with DB ops.
- `src/cc_session_tools/lib/scheduler/cursor.py` — replace `.cursors/*.json` with the `cursors` table.
- `src/cc_session_tools/lib/scheduler/worker.py` — use targeted state ops; keep the lock wrapper.
- `src/cc_session_tools/lib/scheduler/reconcile.py` — per-job `ensure_registered_db` instead of bulk load→save.
- `src/cc_session_tools/lib/scheduler/lock.py` — **import-only** change: `scheduler_dir` now comes from `store` (no logic change).
- `src/cccs_hooks/catchup.py` — throttle via `throttle.py`; import `scheduler_dir` from `store`.
- `src/cc_session_tools/lib/session_gc.py` — **import-only** change (`scheduler_dir` from `store`). Its scheduler-store *scanning* is rewired in Phase 7 (see "Known interactions").
- `src/cc_session_tools/cli/ccst.py` — wire `ccst migrate ccsched`.
- Tests: `test_registry.py`, `test_state.py`, `test_cursor.py`, `test_ccsched_cli.py`, `test_reconcile.py`, `test_worker.py`, `test_catchup_hook.py` — updated where they asserted on flat-file artefacts.

**Not touched:** `lock.py` logic, `ledger.py`, `runner.py`, `notify.py`, `cadence.py`, `duration.py`, `due.py`, `surface.py`, `jobspec.py`.

**One-line wording fix only:** `digest.py`'s `format_digest` parse-error string currently reads `"[cc-scheduler] jobs.toml failed to parse — no jobs ran: {parse_error}"`. Post-migration there is no `jobs.toml`, so this user-facing string is stale (coding standard: comments/messages describe what IS). Change it to a store-agnostic message in Task 8, e.g. `"[cc-scheduler] job registry failed to load — no jobs ran: {parse_error}"`. Nothing else in `digest.py` changes.

---

### Task 1: `store.py` — DB path, schema, connection helper

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/store.py`
- Create: `tests/scheduler/test_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/scheduler/test_store.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import paths
from cc_session_tools.lib.scheduler import store


def test_scheduler_dir_honours_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    assert store.scheduler_dir() == tmp_path / "sched"


def test_scheduler_dir_defaults_to_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CC_SCHEDULER_DIR", raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "dh"))
    assert store.scheduler_dir() == paths.data_home()
    assert store.scheduler_dir() == tmp_path / "dh"


def test_db_path_is_ccsched_db_in_scheduler_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert store.db_path() == tmp_path / "ccsched.db"


def test_connect_creates_all_four_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    conn = store.connect()
    try:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert {"jobs", "job_state", "cursors", "reconcile_throttle"} <= names


def test_connect_applies_wal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    conn = store.connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.scheduler.store'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/scheduler/store.py
"""ccsched.db location and connection. Single source of truth for the scheduler
directory (env CC_SCHEDULER_DIR, else paths.data_home()), the DB path, and the
schema. Every scheduler store module opens the DB through connect() so WAL mode
and the busy-timeout come from lib.db.connect (Phase 1) — never hand-rolled."""
from __future__ import annotations

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
    import os

    raw = os.environ.get(SCHEDULER_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    return paths.data_home()


def db_path() -> Path:
    return scheduler_dir() / "ccsched.db"


def connect(*, readonly: bool = False) -> sqlite3.Connection:
    """Open ccsched.db with the schema applied (WAL + busy-timeout via lib.db)."""
    return db.connect(db_path(), ddl=None if readonly else _DDL, readonly=readonly)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_store.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Repoint `scheduler_dir` importers (no logic change)**

`scheduler_dir()` was defined in `state.py` and imported by `registry.py`, `lock.py`, `cursor.py`, `cccs_hooks/catchup.py`, and `session_gc.py`. It now lives in `store.py`. Update each importer's import line to `from cc_session_tools.lib.scheduler.store import scheduler_dir` (or `from cc_session_tools.lib.scheduler import store` and call `store.scheduler_dir()`), and in later tasks each of these modules is edited anyway. For now only fix `lock.py` and `session_gc.py`, which are otherwise untouched this phase:

In `src/cc_session_tools/lib/scheduler/lock.py` change:
```python
from cc_session_tools.lib.scheduler.state import scheduler_dir
```
to:
```python
from cc_session_tools.lib.scheduler.store import scheduler_dir
```

In `src/cc_session_tools/lib/session_gc.py` change:
```python
from cc_session_tools.lib.scheduler.state import scheduler_dir as _default_scheduler_dir
```
to:
```python
from cc_session_tools.lib.scheduler.store import scheduler_dir as _default_scheduler_dir
```

(State.py will re-export nothing; every reference is repointed as each module is migrated in later tasks.)

- [ ] **Step 6: Run the lock and gc tests to confirm no regression from the import move**

Run: `uv run pytest tests/scheduler/test_lock.py tests/test_session_gc.py -v`
Expected: PASS (unchanged behaviour — `store.scheduler_dir()` returns the same value as before when `CC_SCHEDULER_DIR` is set, which all these tests do). Note: the default now differs (`data_home()` vs `~/.claude/cc-scheduler`), but every test sets the env var, so no test observes the default.

- [ ] **Step 7: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/store.py tests/scheduler/test_store.py \
        src/cc_session_tools/lib/scheduler/lock.py src/cc_session_tools/lib/session_gc.py
git commit -m "feat(scheduler): add store.py — ccsched.db path, schema, connection

scheduler_dir() moves here with its default changed to paths.data_home()
(overview §2). Repoints lock.py and session_gc.py imports."
```

---

### Task 2: `registry.py` → `jobs` table

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/registry.py`
- Modify: `tests/scheduler/test_registry.py`

- [ ] **Step 1: Rewrite the failing tests**

Replace the TOML-specific tests. `test_defaults_applied_for_omitted_fields` and `test_malformed_toml_raises` are **deleted** — with a DB there is no hand-edited file to omit fields from or malform (defaulting happens at the CLI/`validate_job_fields` boundary, covered by `test_ccsched_cli.py`). Keep the round-trip, duplicate, remove, and set-enabled tests; add an ordering test and the R1 concurrency test.

```python
# tests/scheduler/test_registry.py
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler.jobspec import CoalesceKind, validate_job_fields


def _spec(job_id: str = "tesco-shop-check"):
    return validate_job_fields(
        job_id=job_id, cadence="daily@09:00", coalesce="one",
        command=["ccst", "hooks", "run", "check-tesco-due"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
    )


def test_load_missing_registry_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert reg.load_registry() == []


def test_add_then_load_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec())
    loaded = reg.load_registry()
    assert len(loaded) == 1
    assert loaded[0].job_id == "tesco-shop-check"
    assert loaded[0].command == ("ccst", "hooks", "run", "check-tesco-due")
    assert loaded[0].coalesce is CoalesceKind.ONE
    assert loaded[0].surface is True
    assert loaded[0].enabled is True
    assert loaded[0].catchup_window == "7d"
    assert loaded[0].timeout == "60s"


def test_add_duplicate_id_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec())
    with pytest.raises(reg.RegistryError):
        reg.add_job(_spec())


def test_load_preserves_insertion_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    for jid in ("c", "a", "b"):
        reg.add_job(_spec(jid))
    assert [s.job_id for s in reg.load_registry()] == ["c", "a", "b"]
    # An edit keeps position; a remove+re-add moves to the end.
    reg.replace_job(_spec("a"))
    assert [s.job_id for s in reg.load_registry()] == ["c", "a", "b"]
    reg.remove_job("a")
    reg.add_job(_spec("a"))
    assert [s.job_id for s in reg.load_registry()] == ["c", "b", "a"]


def test_replace_unknown_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with pytest.raises(reg.RegistryError):
        reg.replace_job(_spec("ghost"))


def test_remove_and_set_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec("a"))
    reg.add_job(_spec("b"))
    reg.set_enabled("a", False)
    assert {s.job_id: s.enabled for s in reg.load_registry()}["a"] is False
    reg.remove_job("b")
    assert [s.job_id for s in reg.load_registry()] == ["a"]


def test_remove_unknown_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with pytest.raises(reg.RegistryError):
        reg.remove_job("ghost")


def test_set_enabled_unknown_id_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    with pytest.raises(reg.RegistryError):
        reg.set_enabled("ghost", False)


def test_concurrent_edits_to_different_jobs_all_land(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: N threads each editing a DIFFERENT job must all persist — no silent
    last-write-wins loss (the whole-file jobs.toml RMW would drop most of these)."""
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    ids = [f"job-{i}" for i in range(16)]
    for jid in ids:
        reg.add_job(_spec(jid))

    errors: list[Exception] = []

    def flip(jid: str) -> None:
        try:
            reg.set_enabled(jid, False)
        except Exception as exc:  # noqa: BLE001 - captured for assertion
            errors.append(exc)

    threads = [threading.Thread(target=flip, args=(jid,)) for jid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    disabled = {s.job_id: s.enabled for s in reg.load_registry()}
    assert all(disabled[jid] is False for jid in ids)  # every edit landed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_registry.py -v`
Expected: FAIL (old `registry.py` still writes TOML; `test_load_preserves_insertion_order`'s remove+re-add ordering and the concurrency test fail, and imports still reference removed helpers if any).

- [ ] **Step 3: Rewrite `registry.py`**

```python
# src/cc_session_tools/lib/scheduler/registry.py
"""jobs registry, backed by the `jobs` table in ccsched.db. Each mutator is a
single-row INSERT/UPDATE/DELETE inside its own transaction, so concurrent edits
to different jobs never silently clobber each other (R1) — unlike the old
whole-file jobs.toml rewrite. Rows are written already-validated at the CLI
boundary, so load builds JobSpec directly without re-validating."""
from __future__ import annotations

import json
import sqlite3

from cc_session_tools.lib.scheduler import store
from cc_session_tools.lib.scheduler.jobspec import CoalesceKind, JobSpec


class RegistryError(ValueError):
    """Raised for duplicate ids or unknown-id mutations."""


def _spec_from_row(row: sqlite3.Row) -> JobSpec:
    return JobSpec(
        job_id=row["job_id"],
        cadence=row["cadence"],
        coalesce=CoalesceKind(row["coalesce_kind"]),
        command=tuple(json.loads(row["command"])),
        surface=bool(row["surface"]),
        enabled=bool(row["enabled"]),
        catchup_window=row["catchup_window"],
        timeout=row["timeout"],
    )


def load_registry() -> list[JobSpec]:
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT job_id, cadence, coalesce_kind, command, surface, enabled, "
            "catchup_window, timeout FROM jobs ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    return [_spec_from_row(r) for r in rows]


def add_job(spec: JobSpec) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "INSERT INTO jobs (job_id, cadence, coalesce_kind, command, surface, "
            "enabled, catchup_window, timeout) VALUES (?,?,?,?,?,?,?,?)",
            (
                spec.job_id, spec.cadence, spec.coalesce.value,
                json.dumps(list(spec.command)), int(spec.surface), int(spec.enabled),
                spec.catchup_window, spec.timeout,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise RegistryError(f"job id already exists: {spec.job_id!r}") from exc
    finally:
        conn.close()


def replace_job(spec: JobSpec) -> None:
    conn = store.connect()
    try:
        cur = conn.execute(
            "UPDATE jobs SET cadence=?, coalesce_kind=?, command=?, surface=?, "
            "enabled=?, catchup_window=?, timeout=? WHERE job_id=?",
            (
                spec.cadence, spec.coalesce.value, json.dumps(list(spec.command)),
                int(spec.surface), int(spec.enabled), spec.catchup_window,
                spec.timeout, spec.job_id,
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise RegistryError(f"unknown job id: {spec.job_id!r}")
    finally:
        conn.close()


def remove_job(job_id: str) -> None:
    conn = store.connect()
    try:
        cur = conn.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise RegistryError(f"unknown job id: {job_id!r}")
    finally:
        conn.close()


def set_enabled(job_id: str, enabled: bool) -> None:
    conn = store.connect()
    try:
        cur = conn.execute(
            "UPDATE jobs SET enabled=? WHERE job_id=?", (int(enabled), job_id)
        )
        conn.commit()
        if cur.rowcount == 0:
            raise RegistryError(f"unknown job id: {job_id!r}")
    finally:
        conn.close()
```

Notes: `registry_path()`, `_GENERATED_HEADER`, `_DEFAULTS`, `_serialise`, `_toml_str`, `_write`, `_spec_from_table` are all **deleted** (no flat file). Any test or module referencing `registry.registry_path()` is updated in its own task (reconcile test, catchup test — see Tasks 6/9 and the test edits below).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/registry.py tests/scheduler/test_registry.py
git commit -m "feat(scheduler): back registry with jobs table — per-row edits close R1

Concurrent edits to different jobs each land as a single-row UPDATE inside
their own transaction; the whole-file jobs.toml last-write-wins loss is gone."
```

---

### Task 3: `state.py` → `job_state` table (bulk + single-row reads/writes)

Keep the pure helpers (`format_ts`, `parse_ts_or_none`, `next_failure_count`, `JobState`, `InFlight`, `DEFAULT_SUSPEND_THRESHOLD`). Replace the JSON I/O. This task covers the bulk read, the seeding write, and the simple single-statement writes; Task 4 adds the transactional failure path.

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/state.py`
- Modify: `tests/scheduler/test_state.py`

- [ ] **Step 1: Rewrite the failing tests**

The pure `ensure_registered(states, ...)` dict mutator is replaced by `ensure_registered_db(job_id, now)` (used by worker/reconcile). Update the three `ensure_registered` tests accordingly and drop `state.json.tmp`-file assertions.

```python
# tests/scheduler/test_state.py
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import state as st

UTC = timezone.utc


def test_scheduler_dir_honours_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # scheduler_dir now lives in store but is re-exported for callers that used state.
    from cc_session_tools.lib.scheduler import store
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    assert store.scheduler_dir() == tmp_path / "sched"


def test_load_missing_state_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert st.load_all_state() == {}


def test_round_trip_with_in_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    states = {
        "j": st.JobState(
            registered_at="2026-06-20T00:00:00Z",
            last_success="2026-06-20T09:00:00Z",
            last_attempt="2026-06-20T09:00:00Z",
            consecutive_failures=0,
            in_flight=st.InFlight(pid=4321, started_at="2026-06-20T09:00:00Z", instants=3),
        )
    }
    st.save_all_state(states)
    assert st.load_all_state() == states


def test_round_trip_in_flight_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-06-20T00:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=0, in_flight=None)})
    assert st.load_all_state()["j"].in_flight is None


def test_ensure_registered_db_stamps_new_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    now = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    js = st.ensure_registered_db("new-job", now)
    assert js.registered_at == "2026-06-22T08:00:00Z"
    assert js.in_flight is None
    assert js.suspended is False
    assert st.get_state("new-job") == js


def test_ensure_registered_db_leaves_existing_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    existing = st.JobState(registered_at="2026-01-01T00:00:00Z", last_success="2026-02-02T00:00:00Z",
                           last_attempt=None, consecutive_failures=3, in_flight=None)
    st.save_all_state({"j": existing})
    now = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    assert st.ensure_registered_db("j", now) == existing


def test_get_state_missing_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert st.get_state("ghost") is None


def test_set_and_clear_in_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=0, in_flight=None)})
    st.set_in_flight("j", pid=999, started_at="2026-06-22T08:00:00Z", instants=2)
    assert st.get_state("j").in_flight == st.InFlight(pid=999, started_at="2026-06-22T08:00:00Z", instants=2)
    st.clear_in_flight("j")
    assert st.get_state("j").in_flight is None


def test_round_trip_preserves_suspended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    states = {"j": st.JobState(
        registered_at="2026-06-20T00:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=10, in_flight=None, suspended=True)}
    st.save_all_state(states)
    assert st.load_all_state() == states


def test_next_failure_count_increments_below_threshold() -> None:
    assert st.next_failure_count(3, suspended=False, threshold=10) == (4, False, False)


def test_next_failure_count_suspends_at_threshold() -> None:
    assert st.next_failure_count(9, suspended=False, threshold=10) == (10, True, True)


def test_next_failure_count_past_threshold_does_not_resuspend() -> None:
    assert st.next_failure_count(15, suspended=True, threshold=10) == (16, True, False)


def test_clear_suspended_resets_flag_and_leaves_rest_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=12, in_flight=None, suspended=True)})
    st.clear_suspended("j")
    after = st.get_state("j")
    assert after.suspended is False
    assert after.consecutive_failures == 12


def test_clear_suspended_on_unknown_job_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.clear_suspended("ghost")  # must not raise
    assert st.load_all_state() == {}


def test_record_success_resets_streak_preserves_suspended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=st.InFlight(1, "2026-06-22T08:00:00Z", 1),
        suspended=True)})
    st.record_success("j", new_success="2026-06-22T10:00:00Z", attempt_ts="2026-06-22T10:00:00Z")
    after = st.get_state("j")
    assert after.last_success == "2026-06-22T10:00:00Z"
    assert after.consecutive_failures == 0
    assert after.suspended is True            # success does not clear suspension
    assert after.in_flight == st.InFlight(1, "2026-06-22T08:00:00Z", 1)  # untouched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_state.py -v`
Expected: FAIL (JSON-based `state.py` lacks `ensure_registered_db`, `get_state`, `record_success`).

- [ ] **Step 3: Rewrite `state.py`**

```python
# src/cc_session_tools/lib/scheduler/state.py
"""Per-job run state, backed by the `job_state` table in ccsched.db. Pure
helpers (timestamp formatting, next_failure_count) are unchanged; the storage
ops are now targeted single-row reads/writes instead of a whole-file state.json
read-modify-write — the fix for R2 (and the biggest efficiency win in this
phase: a single worker run went from 5 full loads + 4 full saves of every job's
state to a handful of single-row statements touching only its own job)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from cc_session_tools.lib.scheduler import store

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_UTC = timezone.utc
DEFAULT_SUSPEND_THRESHOLD = 10


def format_ts(dt: datetime) -> str:
    return dt.astimezone(_UTC).strftime(_TS_FMT) if dt.tzinfo else dt.strftime(_TS_FMT)


def parse_ts_or_none(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.strptime(value, _TS_FMT).replace(tzinfo=_UTC)


@dataclass(frozen=True, slots=True)
class InFlight:
    pid: int
    started_at: str
    instants: int


@dataclass(frozen=True, slots=True)
class JobState:
    registered_at: str
    last_success: str | None
    last_attempt: str | None
    consecutive_failures: int
    in_flight: InFlight | None = None
    suspended: bool = False


def _row_to_state(row) -> JobState:
    in_flight = (
        None if row["in_flight_pid"] is None
        else InFlight(
            pid=int(row["in_flight_pid"]),
            started_at=row["in_flight_started_at"],
            instants=int(row["in_flight_instants"]),
        )
    )
    return JobState(
        registered_at=row["registered_at"],
        last_success=row["last_success"],
        last_attempt=row["last_attempt"],
        consecutive_failures=int(row["consecutive_failures"]),
        in_flight=in_flight,
        suspended=bool(row["suspended"]),
    )


def load_all_state() -> dict[str, JobState]:
    """Every job's state. Bulk read for `ccsched list` and reconcile's iteration;
    per-job mutators below never load the whole table."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT * FROM job_state").fetchall()
    finally:
        conn.close()
    return {r["job_id"]: _row_to_state(r) for r in rows}


def get_state(job_id: str) -> JobState | None:
    conn = store.connect()
    try:
        row = conn.execute("SELECT * FROM job_state WHERE job_id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_state(row) if row is not None else None


def save_all_state(states: dict[str, JobState]) -> None:
    """Per-row UPSERT of every supplied job's state in one transaction. Used for
    test seeding and the migration script (single-writer contexts). Production
    code paths use the targeted single-row ops below, never this."""
    conn = store.connect()
    try:
        for job_id, js in states.items():
            conn.execute(
                "INSERT INTO job_state (job_id, registered_at, last_success, "
                "last_attempt, consecutive_failures, suspended, in_flight_pid, "
                "in_flight_started_at, in_flight_instants) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET "
                "registered_at=excluded.registered_at, "
                "last_success=excluded.last_success, "
                "last_attempt=excluded.last_attempt, "
                "consecutive_failures=excluded.consecutive_failures, "
                "suspended=excluded.suspended, "
                "in_flight_pid=excluded.in_flight_pid, "
                "in_flight_started_at=excluded.in_flight_started_at, "
                "in_flight_instants=excluded.in_flight_instants",
                (
                    job_id, js.registered_at, js.last_success, js.last_attempt,
                    js.consecutive_failures, int(js.suspended),
                    None if js.in_flight is None else js.in_flight.pid,
                    None if js.in_flight is None else js.in_flight.started_at,
                    None if js.in_flight is None else js.in_flight.instants,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def ensure_registered_db(job_id: str, now: datetime) -> JobState:
    """Stamp registered_at=now for a never-seen job, then return its current
    state. INSERT OR IGNORE is a single write; a hand-added job (registry only)
    thus gets a state row on first sight without back-filling from epoch (§9.1).
    Replaces the old load-mutate-save-everything dance."""
    conn = store.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO job_state (job_id, registered_at, last_success, "
            "last_attempt, consecutive_failures, suspended) VALUES (?,?,NULL,NULL,0,0)",
            (job_id, format_ts(now)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM job_state WHERE job_id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_state(row)


def set_in_flight(job_id: str, *, pid: int, started_at: str, instants: int) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "UPDATE job_state SET in_flight_pid=?, in_flight_started_at=?, "
            "in_flight_instants=? WHERE job_id=?",
            (pid, started_at, instants, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def clear_in_flight(job_id: str) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "UPDATE job_state SET in_flight_pid=NULL, in_flight_started_at=NULL, "
            "in_flight_instants=NULL WHERE job_id=?",
            (job_id,),
        )
        conn.commit()
    finally:
        conn.close()


def clear_suspended(job_id: str) -> None:
    """Clear one job's suspended flag. No-op if the job has no state yet."""
    conn = store.connect()
    try:
        conn.execute("UPDATE job_state SET suspended=0 WHERE job_id=?", (job_id,))
        conn.commit()
    finally:
        conn.close()


def record_success(job_id: str, *, new_success: str, attempt_ts: str) -> None:
    """Advance last_success/last_attempt and reset the failure streak, preserving
    suspended and in_flight. Single-statement write."""
    conn = store.connect()
    try:
        conn.execute(
            "UPDATE job_state SET last_success=?, last_attempt=?, "
            "consecutive_failures=0 WHERE job_id=?",
            (new_success, attempt_ts, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def next_failure_count(
    consecutive_failures: int, *, suspended: bool, threshold: int = DEFAULT_SUSPEND_THRESHOLD
) -> tuple[int, bool, bool]:
    """Pure: (new_consecutive_failures, new_suspended, newly_suspended).
    newly_suspended is True only the instant the threshold is first crossed."""
    new_consecutive = consecutive_failures + 1
    newly_suspended = not suspended and new_consecutive >= threshold
    return new_consecutive, suspended or newly_suspended, newly_suspended
```

`record_failure` (the transactional RMW) and a `record_manual_failure` (for the CLI `run` path) are added in Task 4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/state.py tests/scheduler/test_state.py
git commit -m "feat(scheduler): back job state with job_state table (targeted single-row ops)

Replaces the whole-file state.json read-modify-write with per-row reads/writes.
Keeps the pure helpers (format_ts, next_failure_count) unchanged."
```

---

### Task 4: `state.record_failure` — transactional read-modify-write (R2 core)

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/state.py`
- Modify: `tests/scheduler/test_state.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/scheduler/test_state.py

def test_record_failure_increments_and_reports_new_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success="2026-05-05T00:00:00Z",
        last_attempt=None, consecutive_failures=1, in_flight=None, suspended=False)})
    new_c, new_s, newly = st.record_failure(
        "j", attempt_ts="2026-06-22T10:00:00Z", threshold=10)
    assert (new_c, new_s, newly) == (2, False, False)
    after = st.get_state("j")
    assert after.consecutive_failures == 2
    assert after.last_attempt == "2026-06-22T10:00:00Z"
    assert after.last_success == "2026-05-05T00:00:00Z"   # NOT advanced on failure


def test_record_failure_crossing_threshold_reports_newly_suspended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=9, in_flight=None, suspended=False)})
    new_c, new_s, newly = st.record_failure("j", attempt_ts="2026-06-22T10:00:00Z", threshold=10)
    assert (new_c, new_s, newly) == (10, True, True)
    assert st.get_state("j").suspended is True


def test_record_failure_past_threshold_does_not_renotify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=None, suspended=True)})
    _, new_s, newly = st.record_failure("j", attempt_ts="2026-06-22T10:00:00Z", threshold=10)
    assert (new_s, newly) == (True, False)


def test_concurrent_failure_and_success_on_different_jobs_no_cross_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: concurrent state mutations to DIFFERENT jobs must not clobber each
    other's bookkeeping (the whole-file state.json RMW lost updates here)."""
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    ids = [f"job-{i}" for i in range(16)]
    st.save_all_state({jid: st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=0, in_flight=None) for jid in ids})

    errors: list[Exception] = []

    def fail(jid: str) -> None:
        try:
            st.record_failure(jid, attempt_ts="2026-06-22T10:00:00Z", threshold=10)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=fail, args=(jid,)) for jid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    after = st.load_all_state()
    assert all(after[jid].consecutive_failures == 1 for jid in ids)  # every one landed
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_state.py -k record_failure -v`
Expected: FAIL (`record_failure` does not exist).

- [ ] **Step 3: Add `record_failure` and `record_manual_failure` to `state.py`**

```python
# add to src/cc_session_tools/lib/scheduler/state.py

def record_failure(
    job_id: str, *, attempt_ts: str, threshold: int = DEFAULT_SUSPEND_THRESHOLD
) -> tuple[int, bool, bool]:
    """Read-modify-write the failure counter under BEGIN IMMEDIATE. Returns
    (new_consecutive_failures, new_suspended, newly_suspended). last_success is
    left untouched (a failure never advances it). BEGIN IMMEDIATE takes the write
    lock up front so two concurrent failure records on the same job block-and-retry
    (busy_timeout) rather than deadlocking on a shared-read-lock upgrade (R2)."""
    conn = store.connect()
    conn.isolation_level = None  # manual transaction control
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT consecutive_failures, suspended FROM job_state WHERE job_id=?",
            (job_id,),
        ).fetchone()
        cur_failures = int(row["consecutive_failures"])
        cur_suspended = bool(row["suspended"])
        new_c, new_s, newly = next_failure_count(
            cur_failures, suspended=cur_suspended, threshold=threshold
        )
        conn.execute(
            "UPDATE job_state SET consecutive_failures=?, suspended=?, last_attempt=? "
            "WHERE job_id=?",
            (new_c, int(new_s), attempt_ts, job_id),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    return new_c, new_s, newly


def record_manual_failure(job_id: str, *, attempt_ts: str) -> int:
    """Increment consecutive_failures WITHOUT applying the suspend threshold —
    the behaviour of `ccsched run` (manual foreground run never auto-suspends).
    Returns the new count for the ledger line. Single-statement write, so no
    BEGIN IMMEDIATE needed."""
    conn = store.connect()
    try:
        cur = conn.execute(
            "UPDATE job_state SET consecutive_failures=consecutive_failures+1, "
            "last_attempt=? WHERE job_id=? RETURNING consecutive_failures",
            (attempt_ts, job_id),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    return int(cur["consecutive_failures"])
```

Note: `record_manual_failure` uses `UPDATE ... RETURNING` (SQLite ≥ 3.35, already the Phase 1 minimum). A lone `UPDATE ... x = x + 1` is atomic, so no explicit transaction is needed even though it reads-back via RETURNING — the increment and the returned value are one statement.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/state.py tests/scheduler/test_state.py
git commit -m "feat(scheduler): add record_failure (BEGIN IMMEDIATE) + record_manual_failure

record_failure is the one read-then-write path; IMMEDIATE avoids the
shared-read-lock upgrade deadlock and closes R2 for the failure counter."
```

---

### Task 5: `worker.py` — targeted state ops, lock preserved (R2, R3)

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/worker.py`
- Modify: `tests/scheduler/test_worker.py` (only the helper seed + assertions that read state; the black-box behaviour is unchanged)

> **Before/after efficiency note (put this in the commit body).** Old `_run-job`: `state.load_all_state()` in the registration guard, `set_in_flight`'s internal load+save, `_run_body`'s two `load_all_state()` + one `save_all_state()`, and `clear_in_flight`'s load+save — **≥ 5 full loads + 4 full saves of every job's state per single run**, each save rewriting every job. New: `ensure_registered_db` (1 upsert), `set_in_flight` (1 update), `get_state` (1 read), `record_success`/`record_failure` (1 write), `clear_in_flight` (1 update) — all single-row, touching only this job.

- [ ] **Step 1: Update the test helpers (state seeding)**

`test_worker.py`'s `_seed` uses `st.save_all_state({**st.load_all_state(), ...})`, which still works (bulk UPSERT). No change needed there. The behavioural assertions read `st.load_all_state()[id]` — still valid. Confirm by running the suite against the new `state.py` after the worker rewrite. Add one explicit R3 test:

```python
# append to tests/scheduler/test_worker.py

def test_lock_wraps_sql_state_mutations_r3(monkeypatch: pytest.MonkeyPatch) -> None:
    """R3: the file-based in-flight lock still wraps the (now SQL) state writes.
    A live lock holder means the worker exits without touching state at all."""
    _add("wrapped")
    _seed("wrapped")
    st.scheduler_dir().mkdir(parents=True, exist_ok=True)
    import json as _json
    (st.scheduler_dir() / ".run.wrapped.lock").write_text(
        _json.dumps({"pid": os.getpid(), "started": "x"}))  # held by us (alive)
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("wrapped", instants=1, now=now, runner=_ok_runner)
    # No state row was even created — the worker returned at the lock, before
    # ensure_registered_db.
    assert st.get_state("wrapped") is None
```

Note: `st.scheduler_dir()` is referenced in the existing `test_second_worker_exits_when_lock_held_by_live_pid`. Since `scheduler_dir` moved to `store`, update those references to `store.scheduler_dir()` (import `store`) OR keep a re-export. **Decision: import `store` in the test** and use `store.scheduler_dir()`. Update the existing test's two references accordingly.

- [ ] **Step 2: Run to verify the new R3 test fails against the old worker**

Run: `uv run pytest tests/scheduler/test_worker.py::test_lock_wraps_sql_state_mutations_r3 -v`
Expected: FAIL (old worker calls `state.load_all_state()`/`save_all_state()` and, more importantly, `get_state` does not exist yet if worker not rewritten — or the assertion about no state row fails because the old worker's registration guard writes state before the lock check). Actually the old worker acquires the lock *first*, so this may already pass; the point of the test is to lock in R3 after the rewrite. If it passes pre-rewrite, that is fine — it is a guard, run it again post-rewrite.

- [ ] **Step 3: Rewrite `worker.py`**

```python
# src/cc_session_tools/lib/scheduler/worker.py
"""The detached worker (§9.2) behind `ccsched _run-job <id> --instants k`.

Acquires the per-job in-flight lock (sole overlap guarantee — unchanged from the
flat-file era, R3), stamps in_flight, runs the command with a per-instant
timeout, advances state on success via targeted single-row writes, records the
outcome to the ledger, and ALWAYS clears in_flight + releases the lock."""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta

from cc_session_tools.lib.scheduler import ledger, notify, registry, state
from cc_session_tools.lib.scheduler.cadence import parse_cadence
from cc_session_tools.lib.scheduler.duration import parse_duration
from cc_session_tools.lib.scheduler.due import owed
from cc_session_tools.lib.scheduler.jobspec import CoalesceKind, JobSpec
from cc_session_tools.lib.scheduler.ledger import LedgerEntry, LedgerEvent
from cc_session_tools.lib.scheduler.lock import InFlightLockHeld, in_flight_lock
from cc_session_tools.lib.scheduler.runner import RunOutcome, run_command
from cc_session_tools.lib.scheduler.state import DEFAULT_SUSPEND_THRESHOLD

logger = logging.getLogger(__name__)

Runner = Callable[[tuple[str, ...], timedelta], RunOutcome]
NotifySuspended = Callable[[str, int], bool]


class UnknownJob(ValueError):
    """Raised when _run-job is given an id not in the registry."""


def _load_spec(job_id: str) -> JobSpec:
    for spec in registry.load_registry():
        if spec.job_id == job_id:
            return spec
    raise UnknownJob(f"unknown job id: {job_id!r}")


def _record(spec: JobSpec, event: LedgerEvent, owed_n: int, ran: int,
            outcome: RunOutcome | None, error: str | None,
            consecutive_failures: int = 0) -> None:
    ledger.record(LedgerEntry(
        job_id=spec.job_id, event=event, owed=owed_n, ran=ran,
        exit_code=(outcome.exit_code if outcome else None),
        duration_ms=(outcome.duration_ms if outcome else 0), error=error,
        consecutive_failures=consecutive_failures,
    ))


def _run_body(
    spec: JobSpec, instants: int, now: datetime, runner: Runner,
    notify_suspended: NotifySuspended,
) -> None:
    timeout = parse_duration(spec.timeout)
    cadence = parse_cadence(spec.cadence)
    window = parse_duration(spec.catchup_window)
    js = state.get_state(spec.job_id)
    assert js is not None  # ensure_registered_db ran in run_job before the lock body
    baseline = state.parse_ts_or_none(js.last_success) or state.parse_ts_or_none(js.registered_at)
    assert baseline is not None
    result = owed(cadence, baseline, now, catchup_window=window)
    owed_n = len(result.instants)

    runs = instants if spec.coalesce is CoalesceKind.EACH else 1
    last_outcome: RunOutcome | None = None
    succeeded = 0
    for _ in range(runs):
        last_outcome = runner(spec.command, timeout)
        if last_outcome.timed_out or last_outcome.exit_code != 0:
            break
        succeeded += 1

    failed = last_outcome is None or last_outcome.timed_out or last_outcome.exit_code != 0
    attempt_ts = state.format_ts(now)

    if failed:
        new_consecutive, _new_suspended, newly_suspended = state.record_failure(
            spec.job_id, attempt_ts=attempt_ts, threshold=DEFAULT_SUSPEND_THRESHOLD,
        )
        _record(spec, LedgerEvent.FAIL, owed_n, 0, last_outcome,
                (last_outcome.stderr.strip()[:200] if last_outcome else None)
                or ("timed out" if last_outcome and last_outcome.timed_out else None),
                consecutive_failures=new_consecutive)
        if newly_suspended:
            notify_suspended(spec.job_id, new_consecutive)
            _record(spec, LedgerEvent.SUSPEND, owed_n, 0, None, None,
                    consecutive_failures=new_consecutive)
        return

    if spec.coalesce is CoalesceKind.ONE:
        new_success = state.format_ts(now)
    else:
        new_success = state.format_ts(result.instants[succeeded - 1])
    state.record_success(spec.job_id, new_success=new_success, attempt_ts=attempt_ts)
    event = LedgerEvent.RUN if owed_n <= 1 and succeeded == 1 else LedgerEvent.BACKFILL
    _record(spec, event, owed_n, succeeded, last_outcome, None)


def run_job(
    job_id: str, *, instants: int, now: datetime, runner: Runner = run_command,
    notify_suspended: NotifySuspended = notify.suspended,
) -> None:
    spec = _load_spec(job_id)
    try:
        with in_flight_lock(job_id):
            try:
                # Register the state row before stamping in_flight; a job added
                # via `ccsched add` has a jobs row but no job_state row yet.
                state.ensure_registered_db(job_id, now)
                state.set_in_flight(
                    job_id, pid=os.getpid(), started_at=state.format_ts(now), instants=instants
                )
                _run_body(spec, instants, now, runner, notify_suspended)
            finally:
                state.clear_in_flight(job_id)
    except InFlightLockHeld:
        logger.info("worker for %s exited: lock held by a live holder", job_id)
        return
```

- [ ] **Step 4: Run the worker suite**

Run: `uv run pytest tests/scheduler/test_worker.py -v`
Expected: PASS (all existing behavioural tests plus the new R3 test). If `test_second_worker_exits_when_lock_held_by_live_pid` fails on `st.scheduler_dir()`, apply the `store.scheduler_dir()` import fix from Step 1.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/worker.py tests/scheduler/test_worker.py
git commit -m "refactor(scheduler): worker uses targeted state ops; lock unchanged (R2/R3)

A single _run-job went from >=5 full state loads + 4 full saves (each
rewriting every job) to a handful of single-row statements touching only its
own job. The O_EXCL in-flight lock still wraps all state mutations (R3)."
```

---

### Task 6: `reconcile.py` — per-job `ensure_registered_db`, no bulk save (R4)

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/reconcile.py`
- Modify: `tests/scheduler/test_reconcile.py`

- [ ] **Step 1: Update the parse-error test and add the R4 test**

`test_parse_error_surfaces_and_launches_nothing` wrote a malformed `jobs.toml`; there is no such file now. Replace it with a DB-load-failure simulation (a corrupt DB file), and keep the contract: a registry load failure surfaces via `parse_error` and launches nothing (so the hook never crashes the session). Add the R4 concurrency test.

```python
# replace test_parse_error_surfaces_and_launches_nothing in tests/scheduler/test_reconcile.py
def test_registry_load_failure_surfaces_and_launches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cc_session_tools.lib.scheduler import store
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))
    store.scheduler_dir().mkdir(parents=True, exist_ok=True)
    # A non-SQLite file at the DB path makes registry.load_registry() raise; the
    # reconcile boundary must convert that to parse_error, not crash.
    store.db_path().write_bytes(b"this is not a sqlite database file")
    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = rc.reconcile_and_launch(now=now, spawn=spawn)
    assert result.parse_error is not None
    assert result.launched == []
    assert spawn.calls == []
```

```python
# append to tests/scheduler/test_reconcile.py
def test_reconcile_concurrent_with_worker_setinflight_no_loss_r4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: a reconcile sweep (ensure_registered for several jobs) running
    concurrently with a worker stamping in_flight on a DIFFERENT job must not
    drop the worker's update. With per-row writes this is automatic."""
    import threading
    from cc_session_tools.lib.scheduler import state as st
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))
    # Several never-seen jobs for reconcile to register, plus one job the
    # "worker" stamps in_flight on.
    for i in range(8):
        _add(f"job-{i}")
    _add("worker-job")
    st.save_all_state({"worker-job": st.JobState(
        registered_at="2026-06-20T09:00:00Z", last_success="2026-06-20T09:00:00Z",
        last_attempt=None, consecutive_failures=0, in_flight=None)})

    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    barrier = threading.Barrier(2)

    def do_reconcile() -> None:
        barrier.wait()
        rc.reconcile_and_launch(now=now, spawn=spawn)

    def do_worker_stamp() -> None:
        barrier.wait()
        st.set_in_flight("worker-job", pid=4242, started_at="2026-06-20T10:00:00Z", instants=1)

    t1 = threading.Thread(target=do_reconcile)
    t2 = threading.Thread(target=do_worker_stamp)
    t1.start(); t2.start(); t1.join(); t2.join()

    # The worker's in_flight stamp survived the concurrent reconcile.
    assert st.get_state("worker-job").in_flight == st.InFlight(
        pid=4242, started_at="2026-06-20T10:00:00Z", instants=1)
    # And reconcile registered the never-seen jobs.
    after = st.load_all_state()
    assert all(f"job-{i}" in after for i in range(8))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_reconcile.py -v`
Expected: FAIL — the old `reconcile.py` uses `load_all_state()` + `save_all_state()` (still functional, but the new parse-error test triggers a raw `sqlite3` error the current code does not catch, and the module imports `registry.RegistryError` which the reconcile boundary must still catch).

- [ ] **Step 3: Rewrite the relevant parts of `reconcile.py`**

Change the state handling: drop the single `load_all_state()` + trailing `save_all_state()`; call `state.ensure_registered_db(spec.job_id, now)` per job. Ensure the `try/except registry.RegistryError` around `load_registry()` also catches the DB-load failure. Because `registry.load_registry()` now raises `sqlite3.DatabaseError` (not `RegistryError`) on a corrupt DB, widen the catch: `load_registry` should wrap `sqlite3.DatabaseError` into `RegistryError` at its own boundary so reconcile's existing `except registry.RegistryError` still works. Add that wrap to `registry.load_registry` (small edit), then reconcile only needs the state-handling change.

Add to `registry.load_registry()` (Task 2 module), wrapping the query:
```python
    conn = store.connect()
    try:
        rows = conn.execute(...).fetchall()
    except sqlite3.DatabaseError as exc:
        raise RegistryError(f"ccsched.db is unreadable: {exc}") from exc
    finally:
        conn.close()
```
(If you prefer, fold this into Task 2 when you write `load_registry`. Either way the reconcile test drives it.)

New `reconcile.py` body (state handling only changes):
```python
# src/cc_session_tools/lib/scheduler/reconcile.py  (reconcile_and_launch)
def reconcile_and_launch(
    *,
    now: datetime,
    per_sweep_cap: int = _DEFAULT_LAUNCH_CAP,
    spawn: Spawn = spawn_detached,
) -> ReconcileResult:
    try:
        specs = registry.load_registry()
    except registry.RegistryError as exc:
        return ReconcileResult(launched=[], parse_error=str(exc))

    launched: list[str] = []
    for spec in specs:
        if not spec.enabled:
            continue
        js = state.ensure_registered_db(spec.job_id, now)  # per-row; closes R4
        if js.suspended:
            continue
        if js.in_flight is not None and pid_alive(js.in_flight.pid):
            continue  # fast-path skip; not the correctness guarantee (§9.1)

        cadence = parse_cadence(spec.cadence)
        window = parse_duration(spec.catchup_window)
        baseline = state.parse_ts_or_none(js.last_success) or state.parse_ts_or_none(js.registered_at)
        assert baseline is not None
        result = owed(cadence, baseline, now, catchup_window=window)

        if result.expired_count:
            ledger.record(LedgerEntry(
                job_id=spec.job_id, event=LedgerEvent.SKIP_EXPIRED,
                owed=result.expired_count, ran=0, exit_code=None, duration_ms=0,
                error=None,
            ))
        if not result.instants:
            continue

        if len(launched) >= per_sweep_cap:
            ledger.record(LedgerEntry(
                job_id=spec.job_id, event=LedgerEvent.DEFER,
                owed=len(result.instants), ran=0, exit_code=None, duration_ms=0,
                error="launch cap reached",
            ))
            continue

        k = len(result.instants) if spec.coalesce is CoalesceKind.EACH else 1
        spawn(["ccsched", "_run-job", spec.job_id, "--instants", str(k)])
        ledger.record(LedgerEntry(
            job_id=spec.job_id, event=LedgerEvent.LAUNCH, owed=len(result.instants),
            ran=0, exit_code=None, duration_ms=0, error=None,
        ))
        launched.append(spec.job_id)

    return ReconcileResult(launched=launched, parse_error=None)
```
The trailing `state.save_all_state(states)` and the leading `state.load_all_state()` are **removed** — that whole-file save was exactly the R4 hazard.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_reconcile.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/reconcile.py tests/scheduler/test_reconcile.py \
        src/cc_session_tools/lib/scheduler/registry.py
git commit -m "refactor(scheduler): reconcile registers per-row, no whole-state save (R4)

Drops the load-all -> mutate -> save-all cycle; ensure_registered_db is a
single INSERT OR IGNORE per job, so a concurrent worker's in_flight stamp on
another job can no longer be clobbered. load_registry wraps DB errors as
RegistryError so the hook still degrades to a digest warning."
```

---

### Task 7: `cursor.py` → `cursors` table

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/cursor.py`
- Modify: `tests/scheduler/test_cursor.py`

- [ ] **Step 1: Rewrite the tests (drop `.tmp`-file assertions)**

```python
# tests/scheduler/test_cursor.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import cursor


def test_missing_cursor_defaults_to_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert cursor.read_cursor("session-uuid") == 0


def test_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    cursor.write_cursor("session-uuid", 12)
    assert cursor.read_cursor("session-uuid") == 12


def test_write_cursor_is_idempotent_upsert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    cursor.write_cursor("s", 3)
    cursor.write_cursor("s", 9)
    assert cursor.read_cursor("s") == 9


def test_cursors_are_per_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    cursor.write_cursor("a", 3)
    cursor.write_cursor("b", 7)
    assert cursor.read_cursor("a") == 3
    assert cursor.read_cursor("b") == 7


def test_seed_new_session_only_seeds_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))
    cursor.seed_new_session("u")          # ledger empty -> seeds 0
    cursor.write_cursor("u", 5)           # advance
    cursor.seed_new_session("u")          # must NOT reseed back to 0
    assert cursor.read_cursor("u") == 5
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_cursor.py -v`
Expected: FAIL

- [ ] **Step 3: Rewrite `cursor.py`**

```python
# src/cc_session_tools/lib/scheduler/cursor.py
"""Per-session surfacing cursor (§9.3), backed by the `cursors` table in
ccsched.db. offset = count of catch-up ledger rows already surfaced to this
session. Per-session by design; cross-session dedup is a non-goal. (The ledger
itself is still fires.jsonl until Phase 5, so this offset keeps its exact
current meaning.)"""
from __future__ import annotations

from cc_session_tools.lib.scheduler import ledger, store


def read_cursor(uuid: str) -> int:
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT offset FROM cursors WHERE session_uuid=?", (uuid,)
        ).fetchone()
    finally:
        conn.close()
    return int(row["offset"]) if row is not None else 0


def write_cursor(uuid: str, offset: int) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "INSERT INTO cursors (session_uuid, offset) VALUES (?, ?) "
            "ON CONFLICT(session_uuid) DO UPDATE SET offset=excluded.offset",
            (uuid, offset),
        )
        conn.commit()
    finally:
        conn.close()


def seed_new_session(uuid: str) -> None:
    """Seed this session's cursor at the current end of the ledger if it has none
    yet, so its first digest reflects only activity from this point forward — not
    the entire pre-existing ledger. INSERT OR IGNORE makes it idempotent."""
    conn = store.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO cursors (session_uuid, offset) VALUES (?, ?)",
            (uuid, ledger.current_offset()),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_cursor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/cursor.py tests/scheduler/test_cursor.py
git commit -m "feat(scheduler): back surfacing cursor with cursors table (UPSERT, no per-file)"
```

---

### Task 8: `throttle.py` + `catchup.py` — reconcile throttle to DB (fixes the one non-atomic write)

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/throttle.py`
- Create: `tests/scheduler/test_throttle.py`
- Modify: `src/cccs_hooks/catchup.py`
- Modify: `src/cc_session_tools/lib/scheduler/digest.py` (one-line parse-error wording)
- Modify: `tests/scheduler/test_catchup_hook.py` (import fix + parse-error test)

- [ ] **Step 1: Write the throttle store tests**

```python
# tests/scheduler/test_throttle.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import throttle

UTC = timezone.utc


def test_read_missing_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert throttle.read_last_reconciled("u") is None


def test_stamp_then_read_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    throttle.stamp_reconciled("u", now)
    assert throttle.read_last_reconciled("u") == now


def test_stamp_is_idempotent_upsert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    throttle.stamp_reconciled("u", datetime(2026, 6, 20, 10, 0, tzinfo=UTC))
    later = datetime(2026, 6, 20, 10, 5, tzinfo=UTC)
    throttle.stamp_reconciled("u", later)
    assert throttle.read_last_reconciled("u") == later


def test_per_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    a = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    b = datetime(2026, 6, 20, 11, 0, tzinfo=UTC)
    throttle.stamp_reconciled("a", a)
    throttle.stamp_reconciled("b", b)
    assert throttle.read_last_reconciled("a") == a
    assert throttle.read_last_reconciled("b") == b
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/scheduler/test_throttle.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `throttle.py`**

```python
# src/cc_session_tools/lib/scheduler/throttle.py
"""Per-session reconcile throttle (§13), backed by the `reconcile_throttle`
table in ccsched.db. A single UPSERTed timestamp row per session gates
UserPromptSubmit-triggered reconciles to at most once per throttle window. This
replaces the old .reconcile.<uuid>.ts flat file — the ONE non-atomic write in
the whole subsystem (plain write_text, no tmp-swap) — with a single-row write."""
from __future__ import annotations

from datetime import datetime

from cc_session_tools.lib.scheduler import state, store


def read_last_reconciled(uuid: str) -> datetime | None:
    conn = store.connect()
    try:
        row = conn.execute(
            "SELECT last_reconciled_at FROM reconcile_throttle WHERE session_uuid=?",
            (uuid,),
        ).fetchone()
    finally:
        conn.close()
    return state.parse_ts_or_none(row["last_reconciled_at"]) if row is not None else None


def stamp_reconciled(uuid: str, now: datetime) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "INSERT INTO reconcile_throttle (session_uuid, last_reconciled_at) "
            "VALUES (?, ?) ON CONFLICT(session_uuid) DO UPDATE SET "
            "last_reconciled_at=excluded.last_reconciled_at",
            (uuid, state.format_ts(now)),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/scheduler/test_throttle.py -v`
Expected: PASS

- [ ] **Step 5: Rewire `catchup.py`**

Replace the inline `_throttle_path`, `_should_reconcile`, `_stamp_reconcile` with calls to `throttle`. Import `scheduler_dir` from `store` (only still used by `_log_failure`? No — `_stamp_reconcile` used `state.scheduler_dir().mkdir`; that goes away). Diff:

```python
# src/cccs_hooks/catchup.py — remove the flat-file throttle, use the DB store
from cc_session_tools.lib.scheduler import cursor, ledger, reconcile, state, surface, throttle
# (drop: from pathlib import Path — no longer needed unless used elsewhere)
```

Delete `_throttle_path` entirely. Replace `_should_reconcile` and `_stamp_reconcile`:

```python
def _should_reconcile(event: str, uuid: str, now: datetime) -> bool:
    """SessionStart always reconciles; UserPromptSubmit reconciles at most once
    per throttle window per session (§13)."""
    if event == "SessionStart":
        return True
    last = throttle.read_last_reconciled(uuid)
    return last is None or now - last >= _RECONCILE_THROTTLE


def _stamp_reconcile(uuid: str, now: datetime) -> None:
    throttle.stamp_reconciled(uuid, now)
```

`main()` is otherwise unchanged (it already calls `_should_reconcile` / `_stamp_reconcile`). Note the `except (OSError, ValueError)` catch in `main()` still covers a DB-level `sqlite3.OperationalError`? No — `sqlite3.OperationalError` is not an `OSError`/`ValueError` subclass. Widen the catch to include `sqlite3.Error` so a DB hiccup still degrades to an empty digest instead of crashing the session (§15). Update:

```python
import sqlite3
...
    except (OSError, ValueError, sqlite3.Error) as exc:
        _log_failure(type(exc).__name__)
        _emit("", event)
        return 0
```

- [ ] **Step 6: Fix `test_catchup_hook.py` references and run**

`test_hook_never_raises_on_parse_error` writes a malformed `jobs.toml` via `registry.registry_path()`
— that helper is gone. Replace with a corrupt-DB trigger.

**Design correction (found by adversarial review):** in the pre-migration flat-file design, a
corrupt `jobs.toml` only broke `load_registry` — `cursor.seed_new_session(uuid)` read a
*different* file (`.cursors/<uuid>.json`) and still worked, so `reconcile`'s `parse_error` path
ran and produced a "failed to parse" digest. **After consolidation, registry/cursor/state/throttle
all share one `ccsched.db` file.** `catchup.main()` calls `cursor.seed_new_session(uuid)`
**before** reconcile (live `catchup.py:91`) — so with a corrupt DB, `store.connect()` inside
`seed_new_session` itself raises `sqlite3.DatabaseError` (a `sqlite3.Error` subclass) and is
caught by `main()`'s own top-level `except (OSError, ValueError, sqlite3.Error)` guard (Step 5
above) **before reconcile's `parse_error` digest path is ever reached.** The correct, observable
behaviour for a full-DB-corruption scenario is therefore an **empty degrade** (`_emit("", event)`),
not a "failed to load"/"unreadable" digest string — asserting the latter is asserting unreachable
code:

```python
def test_hook_never_raises_on_corrupt_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from cc_session_tools.lib.scheduler import store
    store.scheduler_dir().mkdir(parents=True, exist_ok=True)
    store.db_path().write_bytes(b"not a sqlite db")
    monkeypatch.setattr(catchup, "_now", lambda: datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(reconcile, "spawn_detached", _Spawn())
    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u", "cwd": "/tmp"})
    rc = catchup.main()
    out = json.loads(sys.stdout... )  # match this test file's existing stdout-capture convention
    assert rc == 0  # never raises, never blocks a session — the §15 invariant
    assert out["hookSpecificOutput"]["additionalContext"] == ""  # empty degrade, not a digest string
```

(Adjust the stdout-capture line to match whichever helper `test_catchup_hook.py` already uses
elsewhere in the file — the point is asserting rc==0 and an empty `additionalContext`, not the
exact capture mechanism.)

The `digest.py` "failed to load" wording fix from Step 5 is still worth keeping — it's exercised by
`reconcile`'s own **unit** test in Task 6 (calling `reconcile_and_launch` directly against a
corrupt DB, which genuinely does reach the `parse_error` branch since it isn't preceded by a
`seed_new_session` call) — just not reachable via this specific hook-level test. Do not delete
Task 6's reconcile-level parse-error test; only this hook-level test's assertion needed
correcting. The remaining catchup tests are unchanged (they set `CC_SCHEDULER_DIR`, so the
throttle DB just works).

Run: `uv run pytest tests/scheduler/test_catchup_hook.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/throttle.py tests/scheduler/test_throttle.py \
        src/cccs_hooks/catchup.py tests/scheduler/test_catchup_hook.py
git commit -m "feat(scheduler): reconcile throttle to reconcile_throttle table

Replaces the one non-atomic write in the subsystem (.reconcile.<uuid>.ts,
plain write_text) with a single-row UPSERT. Hook widens its degrade-to-empty
catch to sqlite3.Error so a DB hiccup never crashes a session."
```

---

### Task 9: CLI `_cmd_run` — narrow transactional state update (the second write path)

The `ccsched run <id>` handler (`ccsched.py:_cmd_run`) has its own inline state read-modify-write, independent of the worker. **Decision (brief asked us to state this explicitly):** `_cmd_run` gets its **own narrow single-statement writes** (`state.ensure_registered_db` + `state.record_success` / `state.record_manual_failure`), **not** the worker's `record_failure`. Reason: `ccsched run` is a manual foreground debug run that today never auto-suspends and never notifies; routing it through `record_failure` (which applies the suspend threshold) would silently change its behaviour, violating the "CLI must not change" contract. The manual path keeps its exact semantics: increment `consecutive_failures`, set `last_attempt`, advance `last_success` only on success, preserve `suspended`.

**Files:**
- Modify: `src/cc_session_tools/cli/ccsched.py`
- Modify: `tests/scheduler/test_ccsched_cli.py` (only the DB-artefact assertion)

- [ ] **Step 1: Update the one artefact-asserting CLI test**

`test_run_job_worker_executes_and_records` reads `state.json`. Rewrite it to read the DB:

```python
def test_run_job_worker_executes_and_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    res = _run(["_run-job", "tesco", "--instants", "1"], sched, hooks)
    assert res.returncode == 0, res.stderr
    assert (hooks / "fires.jsonl").is_file()
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    after = st.load_all_state()["tesco"]
    assert after.last_success is not None
    assert after.in_flight is None
```

The other CLI tests (`test_run_records_ledger`, `test_run_does_not_clear_existing_suspension`, `test_enable_clears_suspension`, etc.) are black-box and unchanged.

- [ ] **Step 2: Run to verify the current state (should still pass, guards behaviour)**

Run: `uv run pytest tests/scheduler/test_ccsched_cli.py -v`
Expected: PASS if worker/state tasks are done; this step guards the rewrite.

- [ ] **Step 3: Rewrite `_cmd_run` and adjust imports in `ccsched.py`**

```python
def _cmd_run(args: argparse.Namespace) -> int:
    specs = {s.job_id: s for s in registry.load_registry()}
    spec: JobSpec | None = specs.get(args.id)
    if spec is None:
        return _err(f"unknown job id: {args.id!r}")
    outcome = run_command(spec.command, parse_duration(spec.timeout))
    now = datetime.now(timezone.utc)
    attempt_ts = state.format_ts(now)
    state.ensure_registered_db(spec.job_id, now)
    failed = outcome.timed_out or outcome.exit_code != 0
    if failed:
        new_consecutive = state.record_manual_failure(spec.job_id, attempt_ts=attempt_ts)
    else:
        state.record_success(spec.job_id, new_success=attempt_ts, attempt_ts=attempt_ts)
        new_consecutive = 0
    ledger.record(ledger.LedgerEntry(
        job_id=spec.job_id,
        event=ledger.LedgerEvent.FAIL if failed else ledger.LedgerEvent.RUN,
        owed=1, ran=0 if failed else 1, exit_code=outcome.exit_code,
        duration_ms=outcome.duration_ms,
        error=(outcome.stderr.strip()[:200] or None) if failed else None,
        consecutive_failures=new_consecutive if failed else 0,
    ))
    print(f"{'failed' if failed else 'ran'} {spec.job_id} (exit={outcome.exit_code})")
    return 1 if failed else 0
```

The `_cmd_list` handler still uses `state.load_all_state()` + `state.parse_ts_or_none` — unchanged (bulk read is fine). `_cmd_sweep`, `_cmd_status`, `_cmd_run_job` unchanged. No other CLI edits. Confirm `state.JobState` / `state.ensure_registered` (pure, removed) are no longer referenced anywhere in `ccsched.py`.

- [ ] **Step 4: Run the full CLI suite**

Run: `uv run pytest tests/scheduler/test_ccsched_cli.py -v`
Expected: PASS (identical stdout/exit codes; only storage backend changed)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccsched.py tests/scheduler/test_ccsched_cli.py
git commit -m "refactor(scheduler): ccsched run uses narrow transactional state writes

The second, independent write path (manual foreground run) now uses the same
targeted single-row helpers, keeping its exact no-auto-suspend semantics."
```

---

### Task 10: Migration script `ccst migrate ccsched`

One-shot, non-destructive (overview §4): read old flat files → write `ccsched.db` → verify row counts → tar-backup the old tree → delete old flat files. `.run.<job-id>.lock` files are **not** migrated (transient; note this in the script and its help). Because the scheduler's default directory moved (`~/.claude/cc-scheduler` → `data_home()`), the script reads from an explicit `--old-dir` (default `~/.claude/cc-scheduler`) and writes to `store.db_path()`.

**Files:**
- Create: `src/cc_session_tools/cli/migrate_ccsched.py`
- Create: `tests/test_migrate_ccsched.py`
- Modify: `src/cc_session_tools/cli/ccst.py` (wire `migrate ccsched`)

- [ ] **Step 1: Write the migration tests**

```python
# tests/test_migrate_ccsched.py
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from cc_session_tools.cli import migrate_ccsched as mig
from cc_session_tools.lib.scheduler import cursor, registry, state, store, throttle


def _seed_old_dir(old: Path) -> None:
    old.mkdir(parents=True, exist_ok=True)
    (old / "jobs.toml").write_text(
        '# header\n[[job]]\nid = "tesco"\ncadence = "daily@09:00"\n'
        'coalesce = "one"\ncommand = ["true"]\nsurface = true\nenabled = true\n'
        'catchup_window = "7d"\ntimeout = "120s"\n'
    )
    (old / "state.json").write_text(json.dumps({
        "tesco": {
            "registered_at": "2026-06-17T09:00:00Z", "last_success": "2026-06-19T09:00:00Z",
            "last_attempt": "2026-06-19T09:00:00Z", "consecutive_failures": 0,
            "suspended": False, "in_flight": None,
        }
    }))
    curs = old / ".cursors"
    curs.mkdir()
    (curs / "sess-a.json").write_text(json.dumps({"offset": 4}))
    (curs / "sess-b.json").write_text(json.dumps({"offset": 9}))
    (old / ".reconcile.sess-a.ts").write_text("2026-06-20T10:00:00Z")
    (old / ".run.tesco.lock").write_text('{"pid": 1, "started": "x"}')  # must NOT migrate


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    _seed_old_dir(old)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "new"))
    rc = mig.run_migration(old_dir=old, db_path=store.db_path(), dry_run=True,
                           backup_dir=tmp_path / "backups")
    assert rc == 0
    assert not store.db_path().exists()
    assert (old / "jobs.toml").is_file()  # untouched


def test_migrates_all_stores_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    _seed_old_dir(old)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "new"))
    rc = mig.run_migration(old_dir=old, db_path=store.db_path(), dry_run=False,
                           backup_dir=tmp_path / "backups")
    assert rc == 0
    # Registry.
    specs = registry.load_registry()
    assert [s.job_id for s in specs] == ["tesco"]
    assert specs[0].command == ("true",)
    # State.
    js = state.get_state("tesco")
    assert js.last_success == "2026-06-19T09:00:00Z"
    # Cursors.
    assert cursor.read_cursor("sess-a") == 4
    assert cursor.read_cursor("sess-b") == 9
    # Throttle.
    assert throttle.read_last_reconciled("sess-a") is not None
    # Backup exists and old tree removed.
    backups = list((tmp_path / "backups").glob("ccsched-*.tar.gz"))
    assert len(backups) == 1
    assert not (old / "jobs.toml").exists()
    assert not (old / ".cursors").exists()
    # The lock file is neither migrated nor deleted context-sensitively; it is
    # simply left out of the DB. (Old dir is tar'd wholesale, then removed.)


def test_backup_contains_old_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    _seed_old_dir(old)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "new"))
    mig.run_migration(old_dir=old, db_path=store.db_path(), dry_run=False,
                      backup_dir=tmp_path / "backups")
    backup = next((tmp_path / "backups").glob("ccsched-*.tar.gz"))
    with tarfile.open(backup) as tf:
        names = tf.getnames()
    assert any(n.endswith("jobs.toml") for n in names)
    assert any(n.endswith("state.json") for n in names)


def test_missing_old_dir_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "new"))
    rc = mig.run_migration(old_dir=tmp_path / "does-not-exist", db_path=store.db_path(),
                           dry_run=False, backup_dir=tmp_path / "backups")
    assert rc == 0
    assert not store.db_path().exists()


def test_rerun_after_migration_is_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    _seed_old_dir(old)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "new"))
    assert mig.run_migration(old_dir=old, db_path=store.db_path(), dry_run=False,
                             backup_dir=tmp_path / "backups") == 0
    # Old dir now gone -> second run is a no-op, not a crash.
    assert mig.run_migration(old_dir=old, db_path=store.db_path(), dry_run=False,
                             backup_dir=tmp_path / "backups") == 0
    assert [s.job_id for s in registry.load_registry()] == ["tesco"]  # not duplicated
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_migrate_ccsched.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `migrate_ccsched.py`**

```python
# src/cc_session_tools/cli/migrate_ccsched.py
"""One-shot migration of the ccsched flat-file stores into ccsched.db.

Exposed via `ccst migrate ccsched`. Non-destructive (overview §4): writes the
DB, verifies row counts against the source files, tar-backs-up the old tree,
and only then removes the old flat files. Never delete-as-you-go.

Reads the OLD scheduler directory (default ~/.claude/cc-scheduler); the DB is
written at the NEW location (store.db_path(), under paths.data_home()). The
.run.<job-id>.lock files are transient and are NOT migrated (they carry no
durable state) — they are simply not read into the DB; the whole old tree is
tar-backed-up before removal, so a lock present at migration time is captured in
the backup and then removed with the rest of the old directory.
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib.scheduler import store
from cc_session_tools.lib.scheduler.jobspec import JobSpec, validate_job_fields
from cc_session_tools.lib.scheduler.state import InFlight, JobState


class MigrationError(RuntimeError):
    pass


def _default_old_dir() -> Path:
    return Path.home() / ".claude" / "cc-scheduler"


def _read_old_jobs(old_dir: Path) -> list[JobSpec]:
    path = old_dir / "jobs.toml"
    if not path.is_file():
        return []
    data = tomllib.loads(path.read_text())
    specs: list[JobSpec] = []
    for t in data.get("job", []):
        specs.append(validate_job_fields(
            job_id=str(t["id"]), cadence=str(t["cadence"]),
            coalesce=str(t.get("coalesce", "one")),
            command=[str(x) for x in t["command"]],
            surface=bool(t.get("surface", True)), enabled=bool(t.get("enabled", True)),
            catchup_window=str(t.get("catchup_window", "7d")),
            timeout=str(t.get("timeout", "120s")),
        ))
    return specs


def _read_old_state(old_dir: Path) -> dict[str, JobState]:
    path = old_dir / "state.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text())
    out: dict[str, JobState] = {}
    for job_id, f in raw.items():
        infl = f.get("in_flight")
        out[job_id] = JobState(
            registered_at=str(f["registered_at"]),
            last_success=f.get("last_success"), last_attempt=f.get("last_attempt"),
            consecutive_failures=int(f.get("consecutive_failures", 0)),
            suspended=bool(f.get("suspended", False)),
            in_flight=None if not isinstance(infl, dict) else InFlight(
                pid=int(infl["pid"]), started_at=str(infl["started_at"]),
                instants=int(infl["instants"])),
        )
    return out


def _read_old_cursors(old_dir: Path) -> dict[str, int]:
    curs = old_dir / ".cursors"
    if not curs.is_dir():
        return {}
    return {p.stem: int(json.loads(p.read_text())["offset"])
            for p in curs.glob("*.json") if p.is_file()}


def _read_old_throttles(old_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not old_dir.is_dir():
        return out
    for p in old_dir.glob(".reconcile.*.ts"):
        if p.is_file():
            uuid = p.name[len(".reconcile."):-len(".ts")]
            out[uuid] = p.read_text().strip()
    return out


def _write_db(specs, states, cursors, throttles, db_path: Path) -> None:
    conn = store.connect()  # creates schema
    try:
        for s in specs:
            conn.execute(
                "INSERT OR IGNORE INTO jobs (job_id, cadence, coalesce_kind, command, "
                "surface, enabled, catchup_window, timeout) VALUES (?,?,?,?,?,?,?,?)",
                (s.job_id, s.cadence, s.coalesce.value, json.dumps(list(s.command)),
                 int(s.surface), int(s.enabled), s.catchup_window, s.timeout),
            )
        for job_id, js in states.items():
            conn.execute(
                "INSERT OR IGNORE INTO job_state (job_id, registered_at, last_success, "
                "last_attempt, consecutive_failures, suspended, in_flight_pid, "
                "in_flight_started_at, in_flight_instants) VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, js.registered_at, js.last_success, js.last_attempt,
                 js.consecutive_failures, int(js.suspended),
                 None if js.in_flight is None else js.in_flight.pid,
                 None if js.in_flight is None else js.in_flight.started_at,
                 None if js.in_flight is None else js.in_flight.instants),
            )
        for uuid, offset in cursors.items():
            conn.execute("INSERT OR IGNORE INTO cursors (session_uuid, offset) VALUES (?,?)",
                         (uuid, offset))
        for uuid, ts in throttles.items():
            conn.execute("INSERT OR IGNORE INTO reconcile_throttle "
                         "(session_uuid, last_reconciled_at) VALUES (?,?)", (uuid, ts))
        conn.commit()
    finally:
        conn.close()


def _verify(specs, states, cursors, throttles) -> None:
    conn = store.connect()
    try:
        def count(table: str) -> int:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        checks = {
            "jobs": (count("jobs"), len(specs)),
            "job_state": (count("job_state"), len(states)),
            "cursors": (count("cursors"), len(cursors)),
            "reconcile_throttle": (count("reconcile_throttle"), len(throttles)),
        }
    finally:
        conn.close()
    for table, (got, want) in checks.items():
        if got < want:
            raise MigrationError(
                f"verify failed for {table}: DB has {got} rows, source had {want}")


def _backup_and_remove(old_dir: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = backup_dir / f"ccsched-{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(old_dir, arcname=old_dir.name)
    import shutil
    shutil.rmtree(old_dir)
    return archive


def run_migration(
    *, old_dir: Path, db_path: Path, dry_run: bool, backup_dir: Path
) -> int:
    if not old_dir.is_dir():
        print(f"No old scheduler dir at {old_dir} — nothing to migrate.")
        return 0

    specs = _read_old_jobs(old_dir)
    states = _read_old_state(old_dir)
    cursors = _read_old_cursors(old_dir)
    throttles = _read_old_throttles(old_dir)

    print(f"Source : {old_dir}")
    print(f"Target : {db_path}")
    print(f"  jobs={len(specs)} state={len(states)} cursors={len(cursors)} "
          f"throttles={len(throttles)}")
    print("  (.run.<id>.lock files are transient and are not migrated)")

    if dry_run:
        print("(dry-run — nothing written)")
        return 0

    _write_db(specs, states, cursors, throttles, db_path)
    try:
        _verify(specs, states, cursors, throttles)
    except MigrationError as exc:
        print(f"ERROR: {exc}\nOld files left in place; DB written but not trusted.",
              file=sys.stderr)
        return 1

    archive = _backup_and_remove(old_dir, backup_dir)
    print(f"Migrated and verified. Old tree backed up to {archive} and removed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Migrate ccsched flat-file stores into ccsched.db "
                    "(non-destructive: verify + tar-backup before removing old files).")
    ap.add_argument("--old-dir", default=None, metavar="PATH",
                    help="Old scheduler dir (default: ~/.claude/cc-scheduler)")
    ap.add_argument("--backup-dir", default=None, metavar="PATH",
                    help="Backup dir (default: <data_home>/migration-backups)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would migrate without writing.")
    args = ap.parse_args(argv)

    old_dir = Path(args.old_dir) if args.old_dir else _default_old_dir()
    backup_dir = (Path(args.backup_dir) if args.backup_dir
                  else store.scheduler_dir() / "migration-backups")
    return run_migration(old_dir=old_dir, db_path=store.db_path(),
                         dry_run=args.dry_run, backup_dir=backup_dir)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_migrate_ccsched.py -v`
Expected: PASS

- [ ] **Step 5: Wire `ccst migrate ccsched`**

In `src/cc_session_tools/cli/ccst.py`: add a `migrate` noun with a `ccsched` verb (mirroring the `tags`/`migrate` pattern), a handler `_cmd_migrate_ccsched`, dispatch in `main()`, and a help line in the module docstring.

```python
# handler (near _cmd_tags_migrate):
def _cmd_migrate_ccsched(args: argparse.Namespace) -> int:
    from cc_session_tools.cli.migrate_ccsched import main as migrate_main
    argv: list[str] = []
    if args.old_dir:
        argv += ["--old-dir", args.old_dir]
    if args.backup_dir:
        argv += ["--backup-dir", args.backup_dir]
    if args.dry_run:
        argv.append("--dry-run")
    return migrate_main(argv)
```

```python
# parser (near the tags block):
    migrate_parser = sub.add_parser("migrate", help="One-shot data-store migrations")
    migrate_sub = migrate_parser.add_subparsers(dest="verb", metavar="<verb>")
    migrate_sub.required = True
    m_ccsched = migrate_sub.add_parser(
        "ccsched",
        help="Migrate ccsched flat-file stores into ccsched.db (non-destructive)")
    m_ccsched.add_argument("--old-dir", default=None, metavar="PATH")
    m_ccsched.add_argument("--backup-dir", default=None, metavar="PATH")
    m_ccsched.add_argument("--dry-run", action="store_true")
```

```python
# dispatch in main():
    if args.noun == "migrate":
        if args.verb == "ccsched":
            sys.exit(_cmd_migrate_ccsched(args))
```

Add a docstring line under "Current subcommands":
```
  migrate ccsched                Migrate ccsched flat-file stores into ccsched.db
                                 (verify + tar-backup old files before removal).
```

- [ ] **Step 6: Smoke-test the CLI wiring**

Run: `uv run python -m cc_session_tools.cli.ccst migrate ccsched --dry-run --old-dir /tmp/does-not-exist`
Expected: prints "No old scheduler dir ... nothing to migrate." exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/cc_session_tools/cli/migrate_ccsched.py tests/test_migrate_ccsched.py \
        src/cc_session_tools/cli/ccst.py
git commit -m "feat(ccst): add 'ccst migrate ccsched' — non-destructive flat-file -> ccsched.db

Verify row counts, tar-backup the old ~/.claude/cc-scheduler tree, then remove
it. .run.<id>.lock files are transient and not migrated."
```

---

## Verification (whole-phase)

- [ ] **Run the full scheduler suite plus the new store/throttle/migration tests**

```bash
uv run pytest tests/scheduler tests/test_store.py tests/test_throttle.py \
              tests/test_migrate_ccsched.py -q
```
Expected: all pass. (Note: `test_store.py` / `test_throttle.py` live under `tests/scheduler/`; adjust the path if you placed them there — the plan places them in `tests/scheduler/`.)

- [ ] **Run the entire repo suite (catch cross-module regressions, esp. `session_gc` and `lock`)**

```bash
uv run pytest -q
```
Expected: green. Pay attention to `tests/test_session_gc.py` — its scheduler stores now scan a directory that (post-migration on a real machine) is empty, but under tests the flat dirs simply do not exist, so it reports 0 scheduler entries without error.

- [ ] **Lint / type-check (match the repo's configured commands)**

```bash
uv run ruff check src/cc_session_tools/lib/scheduler src/cccs_hooks/catchup.py \
       src/cc_session_tools/cli/ccsched.py src/cc_session_tools/cli/migrate_ccsched.py
uv run mypy src/cc_session_tools/lib/scheduler src/cccs_hooks/catchup.py \
       src/cc_session_tools/cli/migrate_ccsched.py
```
(Confirm exact commands from `pyproject.toml` / CI first.) Expected: clean.

- [ ] **Manual CLI smoke test against a real DB**

```bash
export CC_SCHEDULER_DIR=$(mktemp -d)
uv run python -m cc_session_tools.cli.ccsched add --id demo --cadence 'every:6h' --command echo hi
uv run python -m cc_session_tools.cli.ccsched list
uv run python -m cc_session_tools.cli.ccsched run demo
uv run python -m cc_session_tools.cli.ccsched status
ls "$CC_SCHEDULER_DIR"   # expect ccsched.db (and no jobs.toml / state.json)
```
Expected: `add`/`list`/`run`/`status` behave exactly as before; only `ccsched.db` is present.

---

## Known interactions & explicitly deferred

- **`ccst gc report` (session_gc.py) — deferred to Phase 7.** Two of its four stores (`scheduler-reconcile-markers`, `scheduler-cursors`) scan the old flat files, which this phase removes. After migration those two stores read 0 entries. This is benign: `gc report` is report-only (never deletes), so under-reporting orphans cannot cause data loss. Overview §7 assigns the rewire (query `ccsched.db`'s `cursors` / `reconcile_throttle` tables, orphan = `session_uuid` with no transcript) to Phase 7. This phase only repoints its `scheduler_dir` import (Task 1). Do not rewire it here.
- **Ledger stays `fires.jsonl` — Phase 5.** `ledger.py` is untouched; the cursor offset keeps its exact current meaning. Phase 5 moves the ledger to `telemetry.db` and updates `ledger.read_since` / `current_offset`; the cursor table built here is unaffected by that (it stores an integer either way, though Phase 5 may switch it to a monotonic row id — see overview §7 telemetry note).
- **`.run.<job-id>.lock` — unchanged forever.** `lock.py` is not modified; the locks live flat in `scheduler_dir()` (now `data_home()`), created/removed by the worker. The migration does not move them (transient). If a stale lock exists at migration time it is captured in the tar backup and removed with the old tree; a fresh one is created on the next worker run in the new dir.
- **Default-path move affects lock location.** Because `scheduler_dir()` default moved to `data_home()`, on a real machine the lock files now appear under `~/.local/share/claude/` rather than `~/.claude/cc-scheduler/`. Intended (overview §1: everything flat in one root).

---

## Plan review loop & handoff

After implementing, dispatch a `plan-document-reviewer` (or `superpowers:code-reviewer` against the diff) with the plan path and the two source specs; fix and re-review until approved. Then run the full suite once more.

Phase 3 is complete when: `ccsched.db` is the sole backing store for registry/state/cursor/throttle; `lock.py` and `ledger.py` are untouched; the `ccsched` CLI passes its black-box tests unchanged; R1-R4 each have a passing race test; the migration script is tested; and `uv run pytest -q` is green. Phases 2, 4, 5, 6 remain independently mergeable on top of Phase 1; Phase 7 does the final gc-report rewire and install/doctor hookup.
