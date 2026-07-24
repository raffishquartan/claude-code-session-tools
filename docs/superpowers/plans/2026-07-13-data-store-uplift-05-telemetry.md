# Phase 5: telemetry.db Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Read `2026-07-13-data-store-uplift-00-overview.md` and
> `2026-07-13-data-store-uplift-01-shared-infra.md` first — they fix the env-var conventions,
> `lib/db.py`/`lib/paths.py` contracts, and migration-safety rules this plan builds on. This phase
> assumes Phase 1 (`lib/db.py`, `lib/paths.py`) is already merged. It does **not** block on Phase 3
> (`ccsched.db`) despite both touching `lib/scheduler/` — `telemetry.db` is a fully independent
> store; the overview's "Depends on Phase 3" note is about code proximity (both phases touch
> `lib/scheduler/ledger.py`-adjacent modules), not a runtime dependency. If Phase 3 has already
> landed when this phase starts, rebase past it; if not, proceed anyway.

**Goal:** replace `~/.cache/claude/logs/fires.jsonl` (+ up to 3 rotated `.1/.2/.3` slots) with a
single `telemetry.db` SQLite store holding two tables — `telemetry_events` (the generic
PreToolUse/bash-security-review hook-fire family) and `catchup_events` (typed scheduler catch-up
rows) — ship a new `ccst telemetry query` command, and fix the pre-existing rotation/cursor-desync
bug by switching the catch-up cursor from a re-derived row-count offset to a monotonic,
never-reused `INTEGER PRIMARY KEY AUTOINCREMENT` row id.

**Architecture:** one new shared module, `lib/telemetry_store.py`, owns the DDL, `CCCS_HOOKS_DIR`
resolution, and connection helper (single source of truth, consumed by the writer, the trim/query
CLIs, and `lib/scheduler/ledger.py`). `cccs_hooks/telemetry.py` keeps its public
`TelemetryEntry`/`log_event()` API unchanged (so `bash_security_review.py`, `catchup.py`, and
`messaging_deliver.py` need zero source changes) but backs it with a SQL `INSERT` instead of a
JSONL append, and drops the now-dead file-rotation code. `lib/scheduler/ledger.py` keeps its
`record()`/`read_recent()`/`read_since()`/`current_offset()` call signatures unchanged (so
`cursor.py`, `surface.py`, `reconcile.py`, `worker.py`, `ccsched.py` need zero source changes) but
switches catch-up rows from a nested-JSON blob inside a shared JSONL file to typed columns in
their own table with an id-based cursor. Two existing consumers that read `fires.jsonl` directly
(the `bash-hard-deny` PII-exfiltration guard and the `update-command-cache` skill script) are
updated in the same change, per this repo's "update every caller" convention — leaving them stale
would silently reopen a security gap and silently break a skill, respectively.

**Tech Stack:** Python 3.11 stdlib (`sqlite3`, `pathlib`, `argparse`, `tarfile`), pytest,
`monkeypatch`, `cc_session_tools.lib.db` / `lib.paths` (Phase 1).

---

## File Structure

- Create: `src/cc_session_tools/lib/telemetry_store.py`
- Test: `tests/test_telemetry_store.py`
- Modify: `src/cccs_hooks/telemetry.py`
- Modify: `tests/test_telemetry.py`
- Delete: `tests/test_telemetry_rotation.py`
- Modify: `src/cc_session_tools/lib/scheduler/ledger.py`
- Modify: `tests/scheduler/test_ledger.py`
- Modify: `tests/scheduler/test_surface.py`
- Modify: `tests/scheduler/test_catchup_hook.py`
- Modify: `src/cc_session_tools/lib/scheduler/digest.py`
- Modify: `src/cc_session_tools/lib/scheduler/notify.py`
- Modify: `tests/scheduler/test_digest.py`
- Modify: `src/cccs_hooks/telemetry_trim.py`
- Modify: `tests/test_ccst_telemetry_trim.py`
- Create: `src/cccs_hooks/telemetry_query.py`
- Test: `tests/test_ccst_telemetry_query.py`
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `src/cccs_hooks/bash_hard_deny.py`
- Modify: `tests/test_bash_hard_deny.py`
- Modify: `skills/update-command-cache/scripts/update_command_cache.py`
- Create: `skills/update-command-cache/tests/test_update_command_cache.py`
- Modify: `pyproject.toml` (`testpaths`)
- Modify: `tests/scheduler/test_ccsched_cli.py`
- Create: `scripts/migrate_fires_jsonl_to_telemetry_db.py`
- Test: `tests/test_migrate_fires_jsonl_to_telemetry_db.py`

`src/cc_session_tools/lib/scheduler/cursor.py`, `surface.py` (logic, not tests), `reconcile.py`,
`worker.py`, `src/cc_session_tools/cli/ccsched.py`, and `src/cccs_hooks/catchup.py` (logic, not
tests) require **zero** source changes — their calls into `ledger`/`telemetry` go through APIs this
plan keeps stable. This is deliberate and is checked explicitly in Task 14's verification.

---

## Schema

```sql
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
```

**Two tables, not one with nullable columns** — `telemetry_events` (generic PreToolUse/
bash-security-review family) and `catchup_events` (typed scheduler rows) are structurally
different (10 vs 9 columns, no overlap except `ts`) and are never queried together. A single table
with ~19 nullable columns would need a `row_kind` discriminator and would make every catch-up
query (`WHERE job_id = ?`, `WHERE id > ?`) scan past irrelevant generic-hook rows. Two tables also
means `catchup_events.id` is a dense, catch-up-only monotonic sequence — exactly what the cursor
fix needs (see below) — with no filtering required to derive it, unlike the old
`hook == "catchup"` re-filter.

**The desync-bug fix.** The old cursor was a row-count index into a sequence re-derived by
re-filtering the entire flat file on every read (`ledger.py:77-95` in the pre-migration source).
`INTEGER PRIMARY KEY AUTOINCREMENT` never reuses a row id, even after every row is deleted (SQLite
tracks the high-water mark in `sqlite_sequence`) — so `WHERE id > ?` is correct regardless of what
`ccst telemetry trim` has deleted underneath it. Task 3 includes a regression test that reproduces
the exact bug shape from the design brief: write N rows, advance a cursor partway, delete
everything the way a trim would, write M new rows, assert the cursor surfaces exactly the
not-yet-seen rows.

**Migration nicety (conditional — see caveat in Task 13).** Because rows are inserted by the
migration script (Task 13) in original chronological order into an empty table, the Nth catch-up
row ever written gets `id == N` — the same integer the *existing* row-count-based cursor files
(`<scheduler-dir>/.cursors/<uuid>.json`, `{"offset": N}`) already store. So **on a machine whose
`fires.jsonl` has never rotated**, no cursor-file rewrite is needed: an old stored offset of 42
continues to mean "the 42nd catch-up row" after the cutover. This alignment does **not** hold
exactly on a machine where rotation already discarded old catch-up rows before migration ran — see
the "Migration seam caveat" in Task 13 for why that case is bounded and self-healing rather than a
new bug.

---

### Task 1: `lib/telemetry_store.py` — shared schema, path resolution, connect helper

**Files:**
- Create: `src/cc_session_tools/lib/telemetry_store.py`
- Test: `tests/test_telemetry_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_telemetry_store.py
from __future__ import annotations

import re
from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store


def test_db_path_uses_hooks_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    assert telemetry_store.db_path() == tmp_path / "telemetry.db"


def test_db_path_falls_back_to_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CCCS_HOOKS_DIR", raising=False)
    monkeypatch.setattr(telemetry_store, "_DEFAULT_HOOKS_DIR", tmp_path)
    assert telemetry_store.db_path() == tmp_path / "telemetry.db"


def test_db_path_explicit_dir_beats_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "env-dir"))
    explicit = tmp_path / "explicit-dir"
    assert telemetry_store.db_path(explicit) == explicit / "telemetry.db"


def test_connect_creates_both_tables(tmp_path: Path) -> None:
    conn = telemetry_store.connect(tmp_path)
    try:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"telemetry_events", "catchup_events"} <= tables
    finally:
        conn.close()


def test_connect_applies_wal_pragma(tmp_path: Path) -> None:
    conn = telemetry_store.connect(tmp_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_now_iso_is_utc_z_suffixed() -> None:
    ts = telemetry_store.now_iso()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts)


def test_checkpoint_and_vacuum_does_not_raise_on_fresh_db(tmp_path: Path) -> None:
    conn = telemetry_store.connect(tmp_path)
    try:
        telemetry_store.checkpoint_and_vacuum(conn)  # must not raise
    finally:
        conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_telemetry_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.telemetry_store'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/telemetry_store.py
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
bug's root cause)."""
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
# existing repo-wide convention for a module-level default (see
# cccs_hooks.telemetry._DEFAULT_HOOKS_DIR before this migration).
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_telemetry_store.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/telemetry_store.py tests/test_telemetry_store.py
git commit -m "feat(telemetry): add telemetry_store — shared schema/path/connect helper"
```

---

### Task 2: `cccs_hooks/telemetry.py` — SQL-backed writer, drop file rotation

**Files:**
- Modify: `src/cccs_hooks/telemetry.py`
- Modify: `tests/test_telemetry.py`
- Delete: `tests/test_telemetry_rotation.py`

- [ ] **Step 1: Delete the rotation test file**

Rotation (`maybe_rotate`, `_rotate_if_needed`, `_ROTATION_BYTES`, `_ROTATION_KEEP`) has no SQL
equivalent — `ccst telemetry trim` (Task 8) replaces it with a `DELETE ... WHERE ts < ?` /
size-driven delete loop, which is a routine maintenance operation, not something `log_event` does
on every write. Delete the file outright rather than leaving a stub:

```bash
git rm tests/test_telemetry_rotation.py
```

- [ ] **Step 2: Write the failing tests (replace tests/test_telemetry.py in full)**

```python
# tests/test_telemetry.py
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store
from cccs_hooks.telemetry import TelemetryEntry, log_event


# ---------- helpers ----------

def _make_entry(**overrides: object) -> TelemetryEntry:
    base = dict(
        hook="test-hook",
        event="PreToolUse",
        tool="Bash",
        session_id="s1",
        cwd_short="repos/x",
        decision="allow",
        cache="none",
        verdict="safe",
        input_hash="sha256:00",
    )
    base.update(overrides)
    return TelemetryEntry(**base)  # type: ignore[arg-type]


def _rows(hooks_dir: Path) -> list[sqlite3.Row]:
    conn = telemetry_store.connect(hooks_dir)
    try:
        return conn.execute("SELECT * FROM telemetry_events ORDER BY id").fetchall()
    finally:
        conn.close()


# ---------- log_event: row creation ----------

def test_log_event_creates_db_and_inserts_row(tmp_hooks_dir: Path) -> None:
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)
    assert (tmp_hooks_dir / "telemetry.db").exists()
    rows = _rows(tmp_hooks_dir)
    assert len(rows) == 1
    assert rows[0]["hook"] == "test-hook"
    assert rows[0]["verdict"] == "safe"


def test_log_event_ts_is_utc_iso8601(tmp_hooks_dir: Path) -> None:
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)
    ts = _rows(tmp_hooks_dir)[0]["ts"]
    assert ts.endswith("Z")
    datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_log_event_twice_inserts_two_rows(tmp_hooks_dir: Path) -> None:
    entry = _make_entry()
    log_event(entry, hooks_dir=tmp_hooks_dir)
    log_event(entry, hooks_dir=tmp_hooks_dir)
    assert len(_rows(tmp_hooks_dir)) == 2


def test_log_event_preserves_shortened_cwd(tmp_hooks_dir: Path) -> None:
    log_event(_make_entry(cwd_short="repos/cccs"), hooks_dir=tmp_hooks_dir)
    assert _rows(tmp_hooks_dir)[0]["cwd_short"] == "repos/cccs"


# ---------- log_event: never raises ----------

def test_log_event_sqlite_error_does_not_raise(
    tmp_hooks_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_connect(explicit_dir: Path | None = None) -> sqlite3.Connection:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(telemetry_store, "connect", fail_connect)
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)  # must not raise


def test_log_event_os_error_does_not_raise(
    tmp_hooks_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_connect(explicit_dir: Path | None = None) -> sqlite3.Connection:
        raise OSError("No space left on device")

    monkeypatch.setattr(telemetry_store, "connect", fail_connect)
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)  # must not raise


# ---------- log_event: concurrent writes ----------

def test_log_event_concurrent_writes_no_corruption(tmp_hooks_dir: Path) -> None:
    entries = [_make_entry() for _ in range(20)]
    errors: list[Exception] = []

    def write_one(e: TelemetryEntry) -> None:
        try:
            log_event(e, hooks_dir=tmp_hooks_dir)
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=write_one, args=(e,)) for e in entries]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(_rows(tmp_hooks_dir)) == 20


# ---------- CLI entry point ----------

def test_telemetry_cli_log_subcommand(tmp_hooks_dir: Path) -> None:
    import subprocess
    import sys

    hook_input = json.dumps({
        "session_id": "sess-1",
        "cwd": "/example/repos/foo",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    })
    env = {**os.environ, "CCCS_HOOKS_DIR": str(tmp_hooks_dir)}
    result = subprocess.run(
        [
            sys.executable, "-m", "cccs_hooks.telemetry", "log",
            "--hook", "bash-security-review",
            "--event", "PreToolUse",
            "--decision", "allow",
            "--cache", "miss",
            "--verdict", "safe",
            "--input-hash", "sha256:ab",
        ],
        input=hook_input,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).parent.parent),
    )
    assert result.returncode == 0, result.stderr
    rows = _rows(tmp_hooks_dir)
    assert len(rows) == 1
    assert rows[0]["hook"] == "bash-security-review"
    assert rows[0]["session_id"] == "sess-1"
    assert rows[0]["cwd_short"] == "repos/foo"
```

Note: `test_log_event_file_mode_0600` (the old JSONL 0600-file-mode test) is intentionally not
ported — file permissions are now covered by `lib/db.py`'s own `connect()` contract (parent dir
created `mode=0o700`, already tested in Phase 1's `tests/test_db.py`), the same mechanism
`command-cache.db` already relies on. Re-testing that shared contract here would be duplicative.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_telemetry.py -v`
Expected: FAIL — `telemetry.db` never created, `TelemetryEntry`/`log_event` still JSONL-backed

- [ ] **Step 4: Rewrite the implementation**

```python
# src/cccs_hooks/telemetry.py
"""Hook-fire telemetry: writes one row per fire into telemetry.db
(telemetry_events table).

All bash hooks call this module via:
    echo "$INPUT" | python3 -m cccs_hooks.telemetry log --hook NAME ...

Never raises — write failures are logged to stderr and silently suppressed so
a telemetry error never blocks a hook.

Storage lives at CCCS_HOOKS_DIR/telemetry.db (default:
cc_session_tools.lib.paths.data_home()); see lib.telemetry_store for the
schema and path-resolution logic shared with telemetry_trim, telemetry_query,
and lib.scheduler.ledger. Explicit pruning: use ``ccst telemetry trim``.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import sys
from pathlib import Path
from typing import Literal

from cc_session_tools.lib import telemetry_store


@dataclasses.dataclass(frozen=True, slots=True)
class TelemetryEntry:
    hook: str
    event: str
    tool: str
    session_id: str
    cwd_short: str
    decision: Literal["allow", "deny", "annotate"]
    cache: Literal["hit", "miss", "none"]
    verdict: str
    input_hash: str


def log_event(entry: TelemetryEntry, *, hooks_dir: Path | None = None) -> None:
    """Insert one row into telemetry_events. Never raises."""
    try:
        conn = telemetry_store.connect(hooks_dir)
        try:
            conn.execute(
                "INSERT INTO telemetry_events "
                "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    telemetry_store.now_iso(), entry.hook, entry.event, entry.tool,
                    entry.session_id, entry.cwd_short, entry.decision, entry.cache,
                    entry.verdict, entry.input_hash,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as e:
        print(f"[telemetry-warn] log write failed: {e}", file=sys.stderr)


def _shorten_cwd(cwd: str) -> str:
    """Keep last 2 path components to limit PII exposure in the log."""
    parts = Path(cwd).parts
    return "/".join(parts[-2:]) if len(parts) >= 2 else cwd


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cccs_hooks.telemetry")
    sub = p.add_subparsers(dest="cmd")
    log_p = sub.add_parser("log")
    log_p.add_argument("--hook", required=True)
    log_p.add_argument("--event", required=True)
    log_p.add_argument("--decision", required=True)
    log_p.add_argument("--cache", default="none")
    log_p.add_argument("--verdict", default="")
    log_p.add_argument("--input-hash", default="")
    args = p.parse_args(argv)
    if args.cmd != "log":
        p.print_help()
        return 1
    raw = sys.stdin.read()
    try:
        data: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    entry = TelemetryEntry(
        hook=args.hook,
        event=args.event,
        tool=str(data.get("tool_name", "")),
        session_id=str(data.get("session_id", "")),
        cwd_short=_shorten_cwd(str(data.get("cwd", ""))),
        decision=args.decision,
        cache=args.cache,
        verdict=args.verdict,
        input_hash=args.input_hash,
    )
    log_event(entry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Note: `main()` no longer reads `CCCS_HOOKS_DIR` itself and passes an explicit `Path` into
`log_event` — `telemetry_store.hooks_dir(None)` already does that resolution, so duplicating it in
two places was the same kind of drift risk Task 1 exists to close. `log_event`'s `hooks_dir`
parameter stays (callers like `bash_security_review.py` and `catchup.py` pass an explicit
directory for test isolation).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_telemetry.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Run the full suite to check for collateral breakage**

Run: `uv run pytest -q`
Expected: new failures only in files this plan has not touched yet (ledger/surface/catchup-hook/
digest/ccsched-cli/bash-hard-deny tests, and the `_ROTATION_BYTES` import in the now-deleted
rotation test) — each is fixed by a later task in this plan. Confirm the failure list matches
Tasks 3-11's scope before moving on; anything outside that list is a real regression to fix now.

- [ ] **Step 7: Commit**

```bash
git add src/cccs_hooks/telemetry.py tests/test_telemetry.py
git rm tests/test_telemetry_rotation.py
git commit -m "feat(telemetry): back log_event() with telemetry.db, drop JSONL file rotation"
```

---

### Task 3: `lib/scheduler/ledger.py` — typed catch-up rows + id-based cursor fix

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/ledger.py`
- Modify: `tests/scheduler/test_ledger.py`

- [ ] **Step 1: Write the failing tests (replace tests/scheduler/test_ledger.py in full)**

```python
# Generated by: tests/scheduler/test_ledger.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store
from cc_session_tools.lib.scheduler import ledger


def test_record_then_read_recent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    ledger.record(ledger.LedgerEntry(
        job_id="tesco", event=ledger.LedgerEvent.RUN, owed=1, ran=1,
        exit_code=0, duration_ms=42, error=None,
    ))
    rows = ledger.read_recent(job_id="tesco")
    assert len(rows) == 1
    assert rows[0]["job_id"] == "tesco"
    assert rows[0]["event"] == "run"


def test_record_then_read_uses_default_dir_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production default: CCCS_HOOKS_DIR is unset, so the write path
    # (ledger.record) and the read path (ledger.read_recent) must both
    # resolve through telemetry_store's single default. Point that default
    # at tmp_path so the test never touches the real telemetry.db.
    monkeypatch.delenv("CCCS_HOOKS_DIR", raising=False)
    monkeypatch.setattr(telemetry_store, "_DEFAULT_HOOKS_DIR", tmp_path)
    ledger.record(ledger.LedgerEntry(
        job_id="tesco", event=ledger.LedgerEvent.RUN, owed=1, ran=1,
        exit_code=0, duration_ms=42, error=None,
    ))
    assert (tmp_path / "telemetry.db").is_file()
    rows = ledger.read_recent(job_id="tesco")
    assert len(rows) == 1
    assert rows[0]["job_id"] == "tesco"


def test_read_recent_only_sees_catchup_events_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    # A generic telemetry_events row (e.g. bash-security-review) must never
    # leak into catch-up reads — proven structurally, not by a filter, since
    # the two are now separate tables.
    from cccs_hooks.telemetry import TelemetryEntry, log_event
    log_event(TelemetryEntry(
        hook="bash-security-review", event="PreToolUse", tool="Bash",
        session_id="s", cwd_short="x", decision="allow", cache="none",
        verdict="safe", input_hash="sha256:00",
    ))
    ledger.record(ledger.LedgerEntry(
        job_id="cal", event=ledger.LedgerEvent.FAIL, owed=1, ran=0,
        exit_code=2, duration_ms=10, error="boom",
    ))
    rows = ledger.read_recent()
    assert len(rows) == 1
    assert rows[0]["job_id"] == "cal"


def test_launch_event_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    ledger.record(ledger.LedgerEntry(
        job_id="cal", event=ledger.LedgerEvent.LAUNCH, owed=2, ran=0,
        exit_code=None, duration_ms=0, error=None,
    ))
    rows = ledger.read_recent(job_id="cal")
    assert rows[-1]["event"] == "launch"


def test_suspend_event_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    ledger.record(ledger.LedgerEntry(
        job_id="broken-job", event=ledger.LedgerEvent.SUSPEND, owed=0, ran=0,
        exit_code=None, duration_ms=0, error=None, consecutive_failures=10,
    ))
    row = ledger.read_recent(job_id="broken-job")[-1]
    assert row["event"] == "suspend"
    assert row["consecutive_failures"] == 10


def test_read_since_advances_offset_and_ignores_generic_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    from cccs_hooks.telemetry import TelemetryEntry, log_event
    log_event(TelemetryEntry(
        hook="bash-security-review", event="PreToolUse", tool="Bash",
        session_id="s", cwd_short="x", decision="allow", cache="none",
        verdict="safe", input_hash="sha256:00",
    ))
    ledger.record(ledger.LedgerEntry(
        job_id="a", event=ledger.LedgerEvent.RUN, owed=1, ran=1,
        exit_code=0, duration_ms=1, error=None,
    ))
    first, offset = ledger.read_since(0)
    assert [r["job_id"] for r in first] == ["a"]
    assert offset == first[0]["id"]
    # Nothing new since the cursor → empty, offset unchanged.
    again, offset2 = ledger.read_since(offset)
    assert again == []
    assert offset2 == offset
    # A new catch-up entry is surfaced exactly once.
    ledger.record(ledger.LedgerEntry(
        job_id="b", event=ledger.LedgerEvent.FAIL, owed=1, ran=0,
        exit_code=1, duration_ms=2, error="boom",
    ))
    third, offset3 = ledger.read_since(offset2)
    assert [r["job_id"] for r in third] == ["b"]
    assert offset3 > offset2


def test_read_since_clamps_offset_beyond_current_max_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale offset past the current max id must return nothing new, not
    raise or slice negatively."""
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    ledger.record(ledger.LedgerEntry(
        job_id="a", event=ledger.LedgerEvent.RUN, owed=1, ran=1,
        exit_code=0, duration_ms=1, error=None,
    ))
    rows, offset = ledger.read_since(999_999)
    assert rows == []
    assert offset == 999_999


def test_read_since_survives_a_trim_style_delete_no_silent_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the old JSONL rotation/cursor-desync bug: a stale
    row-count offset re-derived from a re-filtered file could silently
    swallow genuinely-new post-rotation rows once the row count climbed back
    past the stored offset. The id-based cursor must not reproduce this:
    deleting old rows (as ccst telemetry trim does) must never make
    read_since() skip rows it has not surfaced yet, no matter what got
    deleted underneath it."""
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    for i in range(5):
        ledger.record(ledger.LedgerEntry(
            job_id=f"job{i}", event=ledger.LedgerEvent.RUN, owed=1, ran=1,
            exit_code=0, duration_ms=1, error=None,
        ))
    first_batch, _ = ledger.read_since(0)
    # Cursor parks after the 3rd of 5 rows (job0, job1, job2 seen).
    partial_offset = first_batch[2]["id"]

    # A trim-style delete removes ALL current rows (old + new-but-unseen
    # alike) — exactly what `ccst telemetry trim --max-age-days 0` would do.
    conn = telemetry_store.connect(tmp_path)
    conn.execute("DELETE FROM catchup_events")
    conn.commit()
    conn.close()

    for i in range(5, 8):
        ledger.record(ledger.LedgerEntry(
            job_id=f"job{i}", event=ledger.LedgerEvent.RUN, owed=1, ran=1,
            exit_code=0, duration_ms=1, error=None,
        ))

    surfaced, _ = ledger.read_since(partial_offset)
    # job3, job4 (unseen before the trim) plus job5, job6, job7 (post-trim) —
    # nothing silently swallowed by the underlying delete.
    assert [r["job_id"] for r in surfaced] == ["job3", "job4", "job5", "job6", "job7"]


def test_current_offset_is_zero_on_empty_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    assert ledger.current_offset() == 0


def test_current_offset_is_max_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    ledger.record(ledger.LedgerEntry(
        job_id="a", event=ledger.LedgerEvent.RUN, owed=1, ran=1,
        exit_code=0, duration_ms=1, error=None,
    ))
    rows, _ = ledger.read_since(0)
    assert ledger.current_offset() == rows[0]["id"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_ledger.py -v`
Expected: FAIL — `ledger.py` still reads/writes the old nested-JSON-in-fires.jsonl shape

- [ ] **Step 3: Rewrite the implementation**

```python
# src/cc_session_tools/lib/scheduler/ledger.py
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
what a trim deletes underneath it."""
from __future__ import annotations

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
    except (OSError, __import__("sqlite3").Error) as e:
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
```

Use a top-level `import sqlite3` instead of the inline `__import__("sqlite3")` shown above once
writing the real file — the inline form is only to keep this snippet's diff small; the actual
implementation should read:

```python
import sqlite3
import sys
...
    except (OSError, sqlite3.Error) as e:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_ledger.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/ledger.py tests/scheduler/test_ledger.py
git commit -m "fix(scheduler): back catch-up ledger with typed catchup_events + id cursor

Closes the rotation/cursor-desync bug: the cursor is now a never-reused
AUTOINCREMENT row id (WHERE id > ?) instead of a row-count index re-derived
by re-filtering a flat file on every read."
```

---

### Task 4: `tests/scheduler/test_surface.py` — migrate raw-fixture helpers off JSONL

**Files:**
- Modify: `tests/scheduler/test_surface.py`

The staleness/summary tests construct catch-up rows with a caller-chosen `ts` (to simulate old or
large backlogs) by hand-writing `fires.jsonl` lines. `ledger.record()` always stamps `ts=now()`, so
these fixtures must insert directly into `catchup_events` instead.

- [ ] **Step 1: Replace `_raw_catchup_line`/`_write_raw_lines` with a direct-insert helper**

```python
# tests/scheduler/test_surface.py — replace the two helpers (old lines 37-64) with:

from cc_session_tools.lib import telemetry_store


def _insert_catchup_row(tmp_path: Path, *, ts: str, job_id: str, event: str, **extra: object) -> None:
    """Insert one catchup_events row with a caller-chosen ts, bypassing
    ledger.record()'s now()-stamping so staleness/backlog-age tests can pin
    exact ages without depending on the real wall clock."""
    conn = telemetry_store.connect(tmp_path / "hooks")
    conn.execute(
        "INSERT INTO catchup_events "
        "(ts, job_id, event, owed, ran, exit_code, duration_ms, error, consecutive_failures) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ts, job_id, event,
            extra.get("owed", 1), extra.get("ran", 0), extra.get("exit_code"),
            extra.get("duration_ms", 1), extra.get("error"),
            extra.get("consecutive_failures", 0),
        ),
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 2: Update the two call sites**

```python
# old:
#   _write_raw_lines(tmp_path, [
#       _raw_catchup_line(ts=old_ts, job_id="cal", event="fail", ran=0, exit_code=1,
#                          error="boom", consecutive_failures=1),
#   ])
# new:
    _insert_catchup_row(
        tmp_path, ts=old_ts, job_id="cal", event="fail", ran=0, exit_code=1,
        error="boom", consecutive_failures=1,
    )
```

```python
# old:
#   lines = [_raw_catchup_line(ts=old_ts, job_id="tesco", event="run", ran=1) for _ in range(150)]
#   lines.append(_raw_catchup_line(ts=recent_ts, job_id="cal", event="fail", ran=0,
#                                    exit_code=1, error="boom", consecutive_failures=1))
#   _write_raw_lines(tmp_path, lines)
# new:
    for _ in range(150):
        _insert_catchup_row(tmp_path, ts=old_ts, job_id="tesco", event="run", ran=1)
    _insert_catchup_row(
        tmp_path, ts=recent_ts, job_id="cal", event="fail", ran=0, exit_code=1,
        error="boom", consecutive_failures=1,
    )
```

- [ ] **Step 3: Run the full file**

Run: `uv run pytest tests/scheduler/test_surface.py -v`
Expected: PASS (all tests, including the two staleness/summary tests using the new helper)

- [ ] **Step 4: Commit**

```bash
git add tests/scheduler/test_surface.py
git commit -m "test(scheduler): migrate surface.py fixture helpers off raw fires.jsonl lines"
```

---

### Task 5: `tests/scheduler/test_catchup_hook.py` — SQL-backed failure-path assertion

**Files:**
- Modify: `tests/scheduler/test_catchup_hook.py`

`catchup.py` itself needs **no source change** — `_log_failure()` still calls
`cccs_hooks.telemetry.log_event()` via `TelemetryEntry`/`hooks_dir=ledger._hooks_dir()` exactly as
before; only the storage backing that call changed in Task 2. Only the test's assertion (which
read raw JSONL text) needs updating.

- [ ] **Step 1: Update `test_failure_path_writes_to_env_ledger_not_real_home`**

```python
# tests/scheduler/test_catchup_hook.py — replace this one test:

def test_failure_path_writes_to_env_ledger_not_real_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The _dirs autouse fixture points CCCS_HOOKS_DIR at tmp_path/hooks. The bad-stdin
    # failure path must log there, NOT to the real ~/.local/share/claude/telemetry.db. If
    # _log_failure ever drops the hooks_dir= argument, log_event falls back to
    # paths.data_home() and this test fails. Guard the real home with a sentinel.
    real_db = Path.home() / ".local" / "share" / "claude" / "telemetry.db"
    before_mtime = real_db.stat().st_mtime if real_db.is_file() else None
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    _capture(monkeypatch)
    assert catchup.main() == 0
    env_db = tmp_path / "hooks" / "telemetry.db"
    assert env_db.is_file()
    conn = sqlite3.connect(str(env_db))
    row = conn.execute(
        "SELECT verdict FROM telemetry_events WHERE hook = 'catchup'"
    ).fetchone()
    conn.close()
    assert row is not None and "catchup-failed:bad-stdin" in row[0]
    after_mtime = real_db.stat().st_mtime if real_db.is_file() else None
    assert after_mtime == before_mtime  # real telemetry.db untouched
```

Add `import sqlite3` to the file's imports.

- [ ] **Step 2: Run the full file**

Run: `uv run pytest tests/scheduler/test_catchup_hook.py -v`
Expected: PASS (all tests)

- [ ] **Step 3: Commit**

```bash
git add tests/scheduler/test_catchup_hook.py
git commit -m "test(scheduler): assert catchup.py's failure path against telemetry.db"
```

---

### Task 6: `digest.py` / `notify.py` — retire the "see fires.jsonl" pointer

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/digest.py`
- Modify: `src/cc_session_tools/lib/scheduler/notify.py`
- Modify: `tests/scheduler/test_digest.py`

Two user-facing digest/notification strings point failed/suspended-job investigators at
`fires.jsonl`, which no longer exists post-migration. Point them at `ccsched status` instead — the
existing command that already surfaces catch-up rows for a job.

- [ ] **Step 1: Update the two assertions in `tests/scheduler/test_digest.py`**

```python
# tests/scheduler/test_digest.py
def test_failure_always_surfaces_even_when_silent() -> None:
    r = JobReport(job_id="calendar-sync", outcome=Outcome.FAILED, surface=False,
                  overdue="2d", ran=0, deferred=0, expired=0, consecutive_failures=2)
    out = format_digest([r])
    assert "calendar-sync failed" in out
    assert "2nd consecutive" in out
    assert "ccsched status" in out
```

```python
def test_suspended_job_always_surfaces_even_when_silent() -> None:
    r = JobReport(job_id="broken-job", outcome=Outcome.SUSPENDED, surface=False,
                  overdue="", ran=0, deferred=0, expired=0, consecutive_failures=10)
    out = format_digest([r])
    assert "broken-job auto-suspended after 10 consecutive failures" in out
    assert "ccsched enable broken-job" in out
    assert "ccsched status" in out
```

- [ ] **Step 2: Run to verify the failures**

Run: `uv run pytest tests/scheduler/test_digest.py -v`
Expected: FAIL on the two updated assertions (`digest.py` still says "fires.jsonl")

- [ ] **Step 3: Update `digest.py` and `notify.py`**

```python
# src/cc_session_tools/lib/scheduler/digest.py:53 — change
#   f"{report.consecutive_failures} consecutive failures{age_suffix} — see fires.jsonl / "
#   f"run `ccsched enable {report.job_id}` after fixing"
# to
            f"{report.consecutive_failures} consecutive failures{age_suffix} — see "
            f"`ccsched status {report.job_id}` / run `ccsched enable {report.job_id}` after fixing"
```

```python
# src/cc_session_tools/lib/scheduler/digest.py:60 — change
#   f"({_ordinal(report.consecutive_failures)} consecutive{age_suffix}) — see fires.jsonl"
# to
            f"({_ordinal(report.consecutive_failures)} consecutive{age_suffix}) — see "
            f"`ccsched status {report.job_id}`"
```

```python
# src/cc_session_tools/lib/scheduler/notify.py:94 — change
#   f"consecutive failures — see fires.jsonl / run "
# to
        f"consecutive failures — see `ccsched status {job_id}` / run "
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_digest.py -v`
Expected: PASS (all tests)

Also check `tests/scheduler/test_notify.py` (if it exists) for a matching literal-string assertion
on the old wording — grep first: `grep -n "fires.jsonl" tests/scheduler/test_notify.py`. Update it
the same way if found.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/digest.py src/cc_session_tools/lib/scheduler/notify.py tests/scheduler/test_digest.py
git commit -m "fix(scheduler): point failure/suspend messages at 'ccsched status', not retired fires.jsonl"
```

---

### Task 7: `tests/scheduler/test_ccsched_cli.py` — assert against telemetry.db

**Files:**
- Modify: `tests/scheduler/test_ccsched_cli.py`

- [ ] **Step 1: Update the two file-existence assertions**

```python
# tests/scheduler/test_ccsched_cli.py

def test_run_records_ledger(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    res = _run(["run", "tesco"], sched, hooks)
    assert res.returncode == 0
    assert (hooks / "telemetry.db").is_file()
```

```python
def test_run_job_worker_executes_and_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # SHARED WITH PHASE 3's Task 9. Phase 3 already migrated this test's state read from the
    # flat `state.json` file to `ccsched.db` (via `st.load_all_state()`), because Phase 3 deletes
    # `state.json`. This phase must NOT regress that — reintroducing `json.loads((sched /
    # "state.json").read_text())` here would raise FileNotFoundError once Phase 3 has landed
    # (the overview mandates Phase 3 before Phase 5). The ONLY change this phase makes to this
    # test is the ledger artefact assertion: `fires.jsonl` -> `telemetry.db`.
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    res = _run(["_run-job", "tesco", "--instants", "1"], sched, hooks)
    assert res.returncode == 0, res.stderr
    assert (hooks / "telemetry.db").is_file()
    # state advanced (last_success set) and in_flight cleared — read from ccsched.db (Phase 3).
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    after = st.load_all_state()["tesco"]
    assert after.last_success is not None
    assert after.in_flight is None
```

- [ ] **Step 2: Run the full file**

Run: `uv run pytest tests/scheduler/test_ccsched_cli.py -v`
Expected: PASS (all tests)

- [ ] **Step 3: Commit**

```bash
git add tests/scheduler/test_ccsched_cli.py
git commit -m "test(scheduler): assert ccsched CLI ledger writes against telemetry.db"
```

---

### Task 8: `cccs_hooks/telemetry_trim.py` — SQL-backed trim (age + size)

**Files:**
- Modify: `src/cccs_hooks/telemetry_trim.py`
- Modify: `tests/test_ccst_telemetry_trim.py`

Design decision: `--max-size` used to *rotate* (never lose data — kept 3 backup slots on disk). A
SQL `DELETE` has no equivalent "move out of the live file but keep it around" option, so
`--max-size` becomes lossy: it deletes the oldest rows (split across both tables) until the on-disk
file is back under the threshold. This is an intentional behaviour change, acceptable because this
is observability data, not irreplaceable content (overview.md §4) — call it out in the CLI help
text and in the module docstring so it's not a silent regression.

- [ ] **Step 1: Write the failing tests (replace tests/test_ccst_telemetry_trim.py in full)**

```python
# tests/test_ccst_telemetry_trim.py
"""Tests for ccst telemetry trim and cccs_hooks.telemetry_trim module."""
from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store
from cccs_hooks.telemetry_trim import enforce_max_size, trim, trim_by_age


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


def _days_ago(n: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_event(hooks_dir: Path, *, ts: str, verdict: str = "safe") -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO telemetry_events "
        "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
        "VALUES (?, 'test-hook', 'PreToolUse', 'Bash', 's1', 'x', 'allow', 'none', ?, '')",
        (ts, verdict),
    )
    conn.commit()
    conn.close()


def _insert_catchup(hooks_dir: Path, *, ts: str) -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO catchup_events "
        "(ts, job_id, event, owed, ran, exit_code, duration_ms, error, consecutive_failures) "
        "VALUES (?, 'job', 'run', 1, 1, 0, 1, NULL, 0)",
        (ts,),
    )
    conn.commit()
    conn.close()


# ---------- trim_by_age ----------

def test_trim_by_age_removes_old_rows_from_both_tables(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(10))
    _insert_event(tmp_path, ts=_days_ago(1))
    _insert_catchup(tmp_path, ts=_days_ago(10))
    _insert_catchup(tmp_path, ts=_days_ago(1))
    conn = telemetry_store.connect(tmp_path)
    kept, removed = trim_by_age(conn, max_age_days=5)
    conn.close()
    assert kept == 2
    assert removed == 2


def test_trim_by_age_keeps_all_recent_rows(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(1))
    conn = telemetry_store.connect(tmp_path)
    kept, removed = trim_by_age(conn, max_age_days=5)
    conn.close()
    assert kept == 1
    assert removed == 0


def test_trim_by_age_no_rows_returns_zero(tmp_path: Path) -> None:
    conn = telemetry_store.connect(tmp_path)
    kept, removed = trim_by_age(conn, max_age_days=5)
    conn.close()
    assert kept == 0
    assert removed == 0


# ---------- enforce_max_size ----------

def test_enforce_max_size_below_threshold_deletes_nothing(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(1))
    conn = telemetry_store.connect(tmp_path)
    db_path = telemetry_store.db_path(tmp_path)
    removed = enforce_max_size(conn, db_path, max_size_mb=10.0)
    conn.close()
    assert removed == 0


def test_enforce_max_size_deletes_oldest_rows_until_under_threshold(tmp_path: Path) -> None:
    conn = telemetry_store.connect(tmp_path)
    db_path = telemetry_store.db_path(tmp_path)
    for i in range(500):
        conn.execute(
            "INSERT INTO telemetry_events "
            "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
            "VALUES (?, 'test-hook', 'PreToolUse', 'Bash', 's1', 'x', 'allow', 'none', ?, '')",
            (_days_ago(500 - i), "x" * 500),  # inflate row size
        )
    conn.commit()
    telemetry_store.checkpoint_and_vacuum(conn)
    before_size = db_path.stat().st_size
    removed = enforce_max_size(conn, db_path, max_size_mb=0.05)
    after_size = db_path.stat().st_size
    remaining = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert removed > 0
    assert after_size < before_size
    assert after_size <= 0.05 * 1024 * 1024 or remaining == 0


# ---------- trim() high-level ----------

def test_trim_age_and_size_combined(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(20))
    _insert_event(tmp_path, ts=_days_ago(1))
    result = trim(max_size_mb=100.0, max_age_days=5, hooks_dir=tmp_path)
    assert result["rows_removed_by_age"] == 1
    assert result["rows_kept_after_age"] == 1
    assert result["rows_removed_by_size"] == 0


def test_trim_dry_run_does_not_modify(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(10))
    _insert_event(tmp_path, ts=_days_ago(1))
    before = telemetry_store.db_path(tmp_path).stat().st_size
    trim(max_age_days=5, hooks_dir=tmp_path, dry_run=True)
    after = telemetry_store.db_path(tmp_path).stat().st_size
    conn = telemetry_store.connect(tmp_path)
    count = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert count == 2
    assert before == after


def test_trim_dry_run_reports_would_remove(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(10))
    _insert_event(tmp_path, ts=_days_ago(1))
    result = trim(max_age_days=5, hooks_dir=tmp_path, dry_run=True)
    assert result.get("would_remove_by_age") == 1


def test_trim_no_db_returns_summary(tmp_path: Path) -> None:
    result = trim(max_age_days=5, hooks_dir=tmp_path)
    assert result["rows_removed_by_age"] == 0


# ---------- CLI integration ----------

def test_telemetry_trim_no_flags_exits_ok(tmp_path: Path) -> None:
    telemetry_store.connect(tmp_path).close()
    result = _run("telemetry", "trim", "--hooks-dir", str(tmp_path))
    assert result.returncode == 0


def test_telemetry_trim_max_age_days(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(20))
    _insert_event(tmp_path, ts=_days_ago(1))
    result = _run("telemetry", "trim", "--hooks-dir", str(tmp_path), "--max-age-days", "5")
    assert result.returncode == 0
    conn = telemetry_store.connect(tmp_path)
    count = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert count == 1


def test_telemetry_trim_dry_run(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(20))
    result = _run(
        "telemetry", "trim", "--hooks-dir", str(tmp_path), "--max-age-days", "5", "--dry-run",
    )
    assert result.returncode == 0
    assert "Dry run" in result.stdout
    conn = telemetry_store.connect(tmp_path)
    count = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_telemetry_trim.py -v`
Expected: FAIL — `telemetry_trim` still operates on `fires.jsonl`

- [ ] **Step 3: Rewrite the implementation**

```python
# src/cccs_hooks/telemetry_trim.py
"""CLI for explicit telemetry pruning: ccst telemetry trim.

Trims telemetry.db (see cc_session_tools.lib.telemetry_store) by:
  --max-age-days <N>   Delete rows older than N days from both
                        telemetry_events and catchup_events.
  --max-size <MB>       Delete the oldest rows (split across both tables)
                        until the on-disk file is at/under this size.
                        LOSSY — unlike the old JSONL scheme (which rotated
                        into up to 3 kept backup slots), a SQL DELETE is
                        permanent. Acceptable because this is observability
                        data, not irreplaceable content.

Both flags are optional and can be combined. Without any flags, no pruning is
done and the tool prints the current file size and row counts.

Designed to be invoked via ``ccst telemetry trim``; can also run directly as
``python -m cccs_hooks.telemetry_trim``.
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

from cc_session_tools.lib import telemetry_store

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_MAX_SIZE_ITERATIONS = 20


def _row_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    events = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    catchup = conn.execute("SELECT COUNT(*) FROM catchup_events").fetchone()[0]
    return events, catchup


def trim_by_age(conn: sqlite3.Connection, max_age_days: int) -> tuple[int, int]:
    """Delete rows older than max_age_days from both tables. Returns (kept, removed)."""
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_age_days)
    ).strftime(_TS_FMT)
    before_events, before_catchup = _row_counts(conn)
    conn.execute("DELETE FROM telemetry_events WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM catchup_events WHERE ts < ?", (cutoff,))
    conn.commit()
    after_events, after_catchup = _row_counts(conn)
    removed = (before_events - after_events) + (before_catchup - after_catchup)
    kept = after_events + after_catchup
    return kept, removed


def enforce_max_size(
    conn: sqlite3.Connection, db_path: Path, max_size_mb: float,
    *, max_iterations: int = _MAX_SIZE_ITERATIONS,
) -> int:
    """Delete the oldest rows (by ts, id tie-break) — a quarter of the
    currently-remaining rows per iteration, split proportionally between
    telemetry_events and catchup_events — until the on-disk file size is
    at/under max_size_mb or there is nothing left to delete. Returns the
    total number of rows deleted."""
    max_bytes = max_size_mb * 1024 * 1024
    total_removed = 0
    for _ in range(max_iterations):
        telemetry_store.checkpoint_and_vacuum(conn)
        if not db_path.exists() or db_path.stat().st_size <= max_bytes:
            break
        events, catchup = _row_counts(conn)
        if events == 0 and catchup == 0:
            break
        events_batch = max(1, events // 4) if events else 0
        catchup_batch = max(1, catchup // 4) if catchup else 0
        if events_batch:
            conn.execute(
                "DELETE FROM telemetry_events WHERE id IN "
                "(SELECT id FROM telemetry_events ORDER BY ts, id LIMIT ?)",
                (events_batch,),
            )
            total_removed += events_batch
        if catchup_batch:
            conn.execute(
                "DELETE FROM catchup_events WHERE id IN "
                "(SELECT id FROM catchup_events ORDER BY ts, id LIMIT ?)",
                (catchup_batch,),
            )
            total_removed += catchup_batch
        conn.commit()
    telemetry_store.checkpoint_and_vacuum(conn)
    return total_removed


def trim(
    *,
    max_size_mb: float | None = None,
    max_age_days: int | None = None,
    hooks_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Run the trim operation. Returns a summary dict."""
    db_path = telemetry_store.db_path(hooks_dir)
    conn = telemetry_store.connect(hooks_dir)
    try:
        summary: dict[str, object] = {
            "path": str(db_path),
            "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
            "rows_removed_by_age": 0,
            "rows_kept_after_age": None,
            "rows_removed_by_size": 0,
        }

        if dry_run:
            if max_age_days is not None:
                cutoff = (
                    datetime.datetime.now(datetime.timezone.utc)
                    - datetime.timedelta(days=max_age_days)
                ).strftime(_TS_FMT)
                would_remove = (
                    conn.execute(
                        "SELECT COUNT(*) FROM telemetry_events WHERE ts < ?", (cutoff,)
                    ).fetchone()[0]
                    + conn.execute(
                        "SELECT COUNT(*) FROM catchup_events WHERE ts < ?", (cutoff,)
                    ).fetchone()[0]
                )
                summary["would_remove_by_age"] = would_remove
            if max_size_mb is not None:
                summary["would_trim_by_size"] = (
                    db_path.exists() and db_path.stat().st_size > max_size_mb * 1024 * 1024
                )
            return summary

        if max_age_days is not None:
            kept, removed = trim_by_age(conn, max_age_days)
            summary["rows_kept_after_age"] = kept
            summary["rows_removed_by_age"] = removed

        if max_size_mb is not None:
            summary["rows_removed_by_size"] = enforce_max_size(conn, db_path, max_size_mb)

        summary["size_bytes"] = db_path.stat().st_size if db_path.exists() else 0
        return summary
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``ccst telemetry trim``."""
    p = argparse.ArgumentParser(
        prog="ccst telemetry trim",
        description="Trim telemetry.db by size and/or age.",
    )
    p.add_argument(
        "--max-size",
        type=float,
        metavar="MB",
        help="Delete the oldest rows until the DB is under this size in MB (lossy — see module docstring)",
    )
    p.add_argument(
        "--max-age-days",
        type=int,
        metavar="N",
        help="Delete rows older than N days",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be done without making changes (default: apply changes)",
    )
    p.add_argument(
        "--hooks-dir",
        default=None,
        metavar="DIR",
        help="telemetry.db directory (default: CCCS_HOOKS_DIR or ~/.local/share/claude/)",
    )
    args = p.parse_args(argv)

    hooks_dir = Path(args.hooks_dir) if args.hooks_dir else None
    db_path = telemetry_store.db_path(hooks_dir)

    print(f"Telemetry DB: {db_path}")
    if db_path.exists():
        size_bytes = db_path.stat().st_size
        print(f"Current size: {size_bytes:,} bytes ({size_bytes / 1024:.1f} KB)")

    if args.max_size is None and args.max_age_days is None:
        print("No trim flags specified. Use --max-size and/or --max-age-days.")
        return 0

    result = trim(
        max_size_mb=args.max_size,
        max_age_days=args.max_age_days,
        hooks_dir=hooks_dir,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print("Dry run — no changes made.")
        if "would_remove_by_age" in result:
            print(f"  Would remove: {result['would_remove_by_age']} row(s) older than {args.max_age_days} day(s)")
        if "would_trim_by_size" in result:
            flag = result["would_trim_by_size"]
            print(f"  Would trim by size: {'yes' if flag else 'no (below threshold)'}")
    else:
        if args.max_age_days is not None:
            print(
                f"  Age trim: kept {result['rows_kept_after_age']} row(s), "
                f"removed {result['rows_removed_by_age']} row(s)"
            )
        if args.max_size is not None:
            print(f"  Size trim: removed {result['rows_removed_by_size']} row(s)")
        new_size = result.get("size_bytes", 0)
        print(f"  New size: {new_size:,} bytes ({new_size / 1024:.1f} KB)")  # type: ignore[str-bytes-safe]

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_telemetry_trim.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/cccs_hooks/telemetry_trim.py tests/test_ccst_telemetry_trim.py
git commit -m "feat(telemetry): rewrite ccst telemetry trim as SQL DELETE (age + lossy size cap)"
```

---

### Task 9: `cccs_hooks/telemetry_query.py` — new `ccst telemetry query` backend

**Files:**
- Create: `src/cccs_hooks/telemetry_query.py`
- Test: `tests/test_ccst_telemetry_query.py`

Design decisions (the one CLI gap the source design spec's gap analysis identified):
- Targets `telemetry_events` only (the generic PreToolUse/bash-security-review hook-fire family).
  Catch-up/job-run events already have a dedicated, typed reader — `ccsched status` — so
  duplicating that here would just be a second, less-typed way to ask the same question.
- **Newest-first** output ordering. The primary use case is "what fired recently" / "any failures
  in the last hour" — newest-first means `--limit N` naturally shows the N most recent regardless
  of total history size, without a full scan-then-tail.
- Flags: `--hook NAME`, `--decision {allow,deny,annotate}`, `--since DURATION` (reuses
  `cc_session_tools.lib.scheduler.duration`'s `<int><s|m|h|d|w>` grammar — no need to invent a
  second duration syntax), `--limit N` (default 50), `--hooks-dir DIR`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ccst_telemetry_query.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store
from cccs_hooks.telemetry_query import query_events


def _insert(
    hooks_dir: Path, *, ts: str, hook: str = "bash-security-review",
    decision: str = "allow", verdict: str = "safe",
) -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO telemetry_events "
        "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
        "VALUES (?, ?, 'PreToolUse', 'Bash', 's1', 'x', ?, 'none', ?, '')",
        (ts, hook, decision, verdict),
    )
    conn.commit()
    conn.close()


def test_query_events_filters_by_hook(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2026-07-01T00:00:00Z", hook="bash-security-review")
    _insert(tmp_path, ts="2026-07-01T00:00:01Z", hook="bash-hard-deny")
    rows = query_events(hook="bash-hard-deny", hooks_dir=tmp_path)
    assert [r["hook"] for r in rows] == ["bash-hard-deny"]


def test_query_events_filters_by_decision(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2026-07-01T00:00:00Z", decision="allow")
    _insert(tmp_path, ts="2026-07-01T00:00:01Z", decision="deny")
    rows = query_events(decision="deny", hooks_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0]["decision"] == "deny"


def test_query_events_filters_by_since(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2020-01-01T00:00:00Z")
    _insert(tmp_path, ts="2099-01-01T00:00:00Z")
    rows = query_events(since_ts="2050-01-01T00:00:00Z", hooks_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0]["ts"] == "2099-01-01T00:00:00Z"


def test_query_events_orders_newest_first(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2026-07-01T00:00:00Z")
    _insert(tmp_path, ts="2026-07-02T00:00:00Z")
    rows = query_events(hooks_dir=tmp_path)
    assert [r["ts"] for r in rows] == ["2026-07-02T00:00:00Z", "2026-07-01T00:00:00Z"]


def test_query_events_respects_limit(tmp_path: Path) -> None:
    for i in range(5):
        _insert(tmp_path, ts=f"2026-07-0{i+1}T00:00:00Z")
    rows = query_events(limit=2, hooks_dir=tmp_path)
    assert len(rows) == 2


# ---------- CLI integration ----------

def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


def test_cli_query_no_events_prints_message(tmp_path: Path) -> None:
    telemetry_store.connect(tmp_path).close()
    result = _run("telemetry", "query", "--hooks-dir", str(tmp_path))
    assert result.returncode == 0
    assert "no matching" in result.stdout.lower()


def test_cli_query_prints_one_line_per_event(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2026-07-01T00:00:00Z", hook="bash-security-review", verdict="safe")
    result = _run("telemetry", "query", "--hooks-dir", str(tmp_path))
    assert result.returncode == 0
    assert "bash-security-review" in result.stdout
    assert "safe" in result.stdout


def test_cli_query_hook_filter(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2026-07-01T00:00:00Z", hook="bash-hard-deny")
    _insert(tmp_path, ts="2026-07-01T00:00:01Z", hook="bash-security-review")
    result = _run("telemetry", "query", "--hooks-dir", str(tmp_path), "--hook", "bash-hard-deny")
    assert "bash-hard-deny" in result.stdout
    assert "bash-security-review" not in result.stdout


def test_cli_query_invalid_decision_rejected(tmp_path: Path) -> None:
    result = _run(
        "telemetry", "query", "--hooks-dir", str(tmp_path), "--decision", "not-a-decision",
    )
    assert result.returncode != 0


def test_cli_query_invalid_since_rejected(tmp_path: Path) -> None:
    result = _run("telemetry", "query", "--hooks-dir", str(tmp_path), "--since", "bogus")
    assert result.returncode != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_telemetry_query.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cccs_hooks.telemetry_query'`

- [ ] **Step 3: Write the implementation**

```python
# src/cccs_hooks/telemetry_query.py
"""CLI for ad hoc telemetry lookups: ccst telemetry query.

Answers "what fired recently" / "any failures in the last hour" against
telemetry_events without grepping a raw file (or, post-migration, without a
raw file to grep at all). Scoped to telemetry_events (the generic
PreToolUse/bash-security-review hook-fire family) — catch-up/job-run events
already have a dedicated, typed reader in ``ccsched status``.

Designed to be invoked via ``ccst telemetry query``; can also run directly as
``python -m cccs_hooks.telemetry_query``.
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

from cc_session_tools.lib import telemetry_store
from cc_session_tools.lib.scheduler.duration import DurationError, parse_duration

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_DEFAULT_LIMIT = 50


def query_events(
    *,
    hook: str | None = None,
    decision: str | None = None,
    verdict: str | None = None,
    since_ts: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    hooks_dir: Path | None = None,
) -> list[sqlite3.Row]:
    """Rows from telemetry_events matching the given filters, newest-first.

    verdict is an exact match (e.g. "safe", "suspicious", "dangerous") — added per
    ccst-migration-and-cli-update-spec.md Section 5.1, which requires filters on
    "hook name, verdict, time range, at minimum"; decision (allow/deny/annotate) and
    verdict are distinct columns and decision cannot substitute for this filter.
    """
    conn = telemetry_store.connect(hooks_dir)
    try:
        clauses: list[str] = []
        params: list[object] = []
        if hook is not None:
            clauses.append("hook = ?")
            params.append(hook)
        if decision is not None:
            clauses.append("decision = ?")
            params.append(decision)
        if verdict is not None:
            clauses.append("verdict = ?")
            params.append(verdict)
        if since_ts is not None:
            clauses.append("ts >= ?")
            params.append(since_ts)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return conn.execute(
            f"SELECT * FROM telemetry_events {where} ORDER BY id DESC LIMIT ?", params
        ).fetchall()
    finally:
        conn.close()


def _format_row(row: sqlite3.Row) -> str:
    return (
        f"{row['ts']}  {row['hook']:<26} {row['event']:<16} {row['decision']:<9} "
        f"cache={row['cache']:<4} tool={row['tool']:<10} verdict={row['verdict']}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ccst telemetry query",
        description="Query telemetry.db's telemetry_events table (newest-first).",
    )
    p.add_argument("--hook", default=None, metavar="NAME", help="Filter by exact hook name")
    p.add_argument(
        "--decision", default=None, choices=["allow", "deny", "annotate"],
        help="Filter by decision",
    )
    p.add_argument(
        "--verdict", default=None, metavar="VERDICT",
        help="Filter by exact verdict text (e.g. safe, suspicious, dangerous)",
    )
    p.add_argument(
        "--since", default=None, metavar="DURATION",
        help="Only events at or after now-DURATION, e.g. 1h, 30m, 2d, 1w",
    )
    p.add_argument(
        "--limit", type=int, default=_DEFAULT_LIMIT, metavar="N",
        help=f"Max rows to print (default: {_DEFAULT_LIMIT})",
    )
    p.add_argument(
        "--hooks-dir", default=None, metavar="DIR",
        help="telemetry.db directory (default: CCCS_HOOKS_DIR or ~/.local/share/claude/)",
    )
    args = p.parse_args(argv)

    since_ts: str | None = None
    if args.since is not None:
        try:
            delta = parse_duration(args.since)
        except DurationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        since_ts = (datetime.datetime.now(datetime.timezone.utc) - delta).strftime(_TS_FMT)

    hooks_dir = Path(args.hooks_dir) if args.hooks_dir else None
    rows = query_events(
        hook=args.hook, decision=args.decision, verdict=args.verdict, since_ts=since_ts,
        limit=args.limit, hooks_dir=hooks_dir,
    )

    if not rows:
        print("No matching telemetry events.")
        return 0

    for row in rows:
        print(_format_row(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_telemetry_query.py -v`
Expected: FAIL on the CLI tests only (`ccst.py` doesn't have a `telemetry query` subcommand yet —
that's Task 10) — the direct `query_events()` unit tests (first 5) should PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cccs_hooks/telemetry_query.py tests/test_ccst_telemetry_query.py
git commit -m "feat(telemetry): add telemetry_query.query_events() — backend for ccst telemetry query"
```

---

### Task 10: `ccst.py` — wire up `telemetry query`, update help text

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`

- [ ] **Step 1: Update the module-level help text (around line 25)**

```python
# src/cc_session_tools/cli/ccst.py — change:
#   telemetry trim                 Trim ~/.cache/claude/logs/fires.jsonl by size / age.
# to:
"""
  telemetry trim                 Trim telemetry.db by size / age (see ccst telemetry trim --help).
  telemetry query                Query recent hook fires from telemetry.db (see
                                 ccst telemetry query --help).
"""
```

(Keep it inline with the surrounding docstring block — insert the `telemetry query` line directly
below the existing `telemetry trim` line.)

- [ ] **Step 2: Update the trim subparser's help text (around line 1109-1135)**

```python
    telemetry_trim_parser = telemetry_sub.add_parser(
        "trim",
        help="Trim telemetry.db by size and/or age",
    )
```

(`--hooks-dir`'s help string also needs its default description updated: replace `"Logs directory
(default: ~/.cache/claude/logs/)"` with `"telemetry.db directory (default: CCCS_HOOKS_DIR or
~/.local/share/claude/)"`.)

- [ ] **Step 3: Add the query subparser (immediately after the trim subparser block)**

```python
    telemetry_query_parser = telemetry_sub.add_parser(
        "query",
        help="Query recent hook fires from telemetry.db's telemetry_events table",
    )
    telemetry_query_parser.add_argument(
        "--hook", default=None, metavar="NAME", help="Filter by exact hook name",
    )
    telemetry_query_parser.add_argument(
        "--decision", default=None, choices=["allow", "deny", "annotate"],
        help="Filter by decision",
    )
    telemetry_query_parser.add_argument(
        "--verdict", default=None, metavar="VERDICT",
        help="Filter by exact verdict text (e.g. safe, suspicious, dangerous)",
    )
    telemetry_query_parser.add_argument(
        "--since", default=None, metavar="DURATION",
        help="Only events at or after now-DURATION, e.g. 1h, 30m, 2d, 1w",
    )
    telemetry_query_parser.add_argument(
        "--limit", type=int, default=50, metavar="N", help="Max rows to print (default: 50)",
    )
    telemetry_query_parser.add_argument(
        "--hooks-dir", default=None, metavar="DIR",
        help="telemetry.db directory (default: CCCS_HOOKS_DIR or ~/.local/share/claude/)",
    )
```

- [ ] **Step 4: Add the dispatcher function (near `_cmd_telemetry_trim`)**

```python
def _cmd_telemetry_query(args: argparse.Namespace) -> int:
    from cccs_hooks.telemetry_query import main as query_main

    argv: list[str] = []
    if args.hook is not None:
        argv += ["--hook", args.hook]
    if args.decision is not None:
        argv += ["--decision", args.decision]
    if args.verdict is not None:
        argv += ["--verdict", args.verdict]
    if args.since is not None:
        argv += ["--since", args.since]
    if args.limit != 50:
        argv += ["--limit", str(args.limit)]
    if getattr(args, "hooks_dir", None):
        argv += ["--hooks-dir", args.hooks_dir]

    return query_main(argv)
```

- [ ] **Step 5: Wire the dispatch (in `main()`, next to the existing `telemetry` block)**

```python
    if args.noun == "telemetry":
        if args.verb == "trim":
            sys.exit(_cmd_telemetry_trim(args))
        if args.verb == "query":
            sys.exit(_cmd_telemetry_query(args))
```

- [ ] **Step 6: Run the full `ccst telemetry query` test file**

Run: `uv run pytest tests/test_ccst_telemetry_query.py -v`
Expected: PASS (all tests, including the CLI-integration ones that were failing after Task 9)

- [ ] **Step 7: Run the trim test file too (help-text/docstring changes only, should be unaffected)**

Run: `uv run pytest tests/test_ccst_telemetry_trim.py -v`
Expected: PASS (all tests)

- [ ] **Step 8: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py
git commit -m "feat(cli): wire up ccst telemetry query"
```

---

### Task 11: `bash_hard_deny.py` — update the PII-exfiltration guard for telemetry.db

**Files:**
- Modify: `src/cccs_hooks/bash_hard_deny.py`
- Modify: `tests/test_bash_hard_deny.py`

Check #12 blocks direct reads of `fires.jsonl*` to prevent prompt-injection-driven exfiltration of
session/command-hash data. Once telemetry moves to SQLite, that guard silently stops protecting
anything — a command like `sqlite3 ~/.local/share/claude/telemetry.db "select session_id from
telemetry_events"` would sail straight through. Close this gap in the same change (the existing
`fires.jsonl*` patterns are left in place too, harmlessly — any leftover rotated slots from before
a machine's migration script has run are still worth blocking).

- [ ] **Step 1: Add the failing tests**

```python
# tests/test_bash_hard_deny.py — add alongside the existing "fires.jsonl telemetry-log reads" block

def test_blocks_sqlite3_cli_read_of_telemetry_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CCCS_FIRES_ACCESS", raising=False)
    rc, _out, _err = _run_bash(
        monkeypatch, 'sqlite3 ~/.local/share/claude/telemetry.db "select * from telemetry_events"'
    )
    assert rc == 2


def test_allows_sqlite3_telemetry_db_with_access_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_FIRES_ACCESS", "1")
    _assert_allowed(
        monkeypatch, 'sqlite3 ~/.local/share/claude/telemetry.db "select * from telemetry_events"'
    )


def test_blocks_sqlite3_telemetry_db_basename_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # No path prefix, just the basename — still catchable.
    monkeypatch.delenv("CCCS_FIRES_ACCESS", raising=False)
    rc, _out, _err = _run_bash(monkeypatch, "sqlite3 telemetry.db '.dump'")
    assert rc == 2


def test_allows_sqlite3_unrelated_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CCCS_FIRES_ACCESS", raising=False)
    _assert_allowed(monkeypatch, "sqlite3 /tmp/scratch.db '.tables'")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bash_hard_deny.py -k telemetry_db -v`
Expected: FAIL — no `telemetry.db` pattern exists yet in `check_command`

- [ ] **Step 3: Update the guard**

```python
# src/cccs_hooks/bash_hard_deny.py — near _FIRES_READ_RE / _FIRES_LOG_PATH:

from cc_session_tools.lib.telemetry_store import db_path as _telemetry_db_path

# Direct reads of the telemetry log. Two eras are covered: the retired
# fires.jsonl* flat files (basename-based, catches rotated slots too) and
# the current telemetry.db (any sqlite3 CLI invocation naming it, regardless
# of the query — a read-only SELECT leaks the same session/hash data a
# schema dump or full-table read would).
_FIRES_READ_RE = re.compile(r"(cat|head|tail|less|more|hexdump|xxd)\s+.*fires.*\.jsonl")
_TELEMETRY_DB_READ_RE = re.compile(r"sqlite3\s+.*telemetry\.db")
_FIRES_LOG_PATH = _telemetry_db_path().parent / "fires.jsonl"
_TELEMETRY_DB_PATH = _telemetry_db_path()
```

```python
    # 12. Direct reads of the telemetry log (fires.jsonl* or telemetry.db).
    #
    # Historically ~/.cache/claude/logs/fires.jsonl*; now telemetry.db (see
    # cc_session_tools.lib.telemetry_store). Both contain session metadata and
    # command hashes. Direct reads are blocked to prevent prompt-injection from
    # harvesting this data. Skills that legitimately need it — ``update-command-cache``
    # (skills/update-command-cache/scripts/update_command_cache.py) and
    # ``analyse-cc-usage`` — set CCCS_FIRES_ACCESS=1 before invoking the read.
    if os.environ.get("CCCS_FIRES_ACCESS", "0") != "1":
        if _FIRES_READ_RE.search(command):
            return (
                "BLOCKED: Direct reads of the hook telemetry log (fires.jsonl*) are "
                "blocked to prevent credential/session-data exfiltration via prompt "
                "injection. Use the update-command-cache or analyse-cc-usage skill, "
                f"or set CCCS_FIRES_ACCESS=1 in the environment. (Log lives at "
                f"{_FIRES_LOG_PATH}.)"
            )
        if _TELEMETRY_DB_READ_RE.search(command):
            return (
                "BLOCKED: Direct sqlite3 reads of telemetry.db are blocked to prevent "
                "credential/session-data exfiltration via prompt injection. Use "
                "`ccst telemetry query`, the update-command-cache skill, or "
                f"set CCCS_FIRES_ACCESS=1 in the environment. (DB lives at "
                f"{_TELEMETRY_DB_PATH}.)"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bash_hard_deny.py -v`
Expected: PASS (all tests, including the pre-existing `fires.jsonl` cases — unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/cccs_hooks/bash_hard_deny.py tests/test_bash_hard_deny.py
git commit -m "fix(security): extend bash-hard-deny's telemetry-log guard to sqlite3 reads of telemetry.db"
```

---

### Task 12: `update-command-cache` skill — read telemetry.db instead of fires.jsonl

**Files:**
- Modify: `skills/update-command-cache/scripts/update_command_cache.py`
- Create: `skills/update-command-cache/tests/test_update_command_cache.py`
- Modify: `pyproject.toml` (add the new tests dir to `testpaths`, following the existing
  `skills/<name>/tests` precedent used by `move-session`, `list-empty-sessions`, etc.)

This script currently reads `~/.cache/claude/logs/fires.jsonl` directly via
`cccs_hooks.telemetry._DEFAULT_HOOKS_DIR`. Left unchanged, it would silently stop finding any
candidates post-migration (the file it reads is never written again) — exactly the kind of silent
breakage the "update every caller in the same change" rule exists to prevent.

- [ ] **Step 1: Write the failing test**

```python
# skills/update-command-cache/tests/test_update_command_cache.py
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from cc_session_tools.lib import telemetry_store  # noqa: E402
import update_command_cache as ucc  # noqa: E402


def _insert(
    hooks_dir: Path, *, hook: str, verdict: str, cache: str, input_hash: str,
    ts: str = "2026-07-01T00:00:00Z", session_id: str = "s1",
) -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO telemetry_events "
        "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
        "VALUES (?, ?, 'PreToolUse', 'Bash', ?, 'x', 'allow', ?, ?, ?)",
        (ts, hook, session_id, cache, verdict, input_hash),
    )
    conn.commit()
    conn.close()


def test_collect_candidates_finds_uncached_safe_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: None)
    _insert(tmp_path, hook="bash-security-review", verdict="safe", cache="miss", input_hash="sha256:aa")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    candidates = ucc.collect_candidates(rows)
    assert [c["sha"] for c in candidates] == ["aa"]


def test_collect_candidates_skips_non_bash_security_review_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: None)
    _insert(tmp_path, hook="bash-hard-deny", verdict="safe", cache="miss", input_hash="sha256:bb")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    assert ucc.collect_candidates(rows) == []


def test_collect_candidates_skips_cache_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: None)
    _insert(tmp_path, hook="bash-security-review", verdict="safe", cache="hit", input_hash="sha256:cc")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    assert ucc.collect_candidates(rows) == []


def test_collect_candidates_skips_already_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ucc, "cache_lookup", lambda sha: object())  # already cached
    _insert(tmp_path, hook="bash-security-review", verdict="safe", cache="miss", input_hash="sha256:dd")
    rows = ucc.read_telemetry_events(hooks_dir=tmp_path)
    assert ucc.collect_candidates(rows) == []
```

Add `skills/update-command-cache/tests` to `pyproject.toml`'s `testpaths`:

```toml
testpaths = ["tests", "tests/scheduler", "skills/move-session/tests", "skills/list-empty-sessions/tests", "skills/delete-sessions/tests", "skills/generate-8digit-code/tests", "skills/reduce-persistent-context/tests", "skills/update-command-cache/tests"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest skills/update-command-cache/tests/ -v`
Expected: FAIL — `update_command_cache.py` has no `read_telemetry_events` function yet

- [ ] **Step 3: Update the implementation**

```python
# skills/update-command-cache/scripts/update_command_cache.py
# Replace the file header docstring, imports, _FIRES_PATH/_read_fires, and cmd_list's read step:

"""Curate the bash-security-review command cache.

Reads telemetry.db's telemetry_events table (hook='bash-security-review'),
surfaces safe-verdict commands not yet in the cache, prompts for approval,
and records approved ones.

Usage:
    CCCS_FIRES_ACCESS=1 python3 update_command_cache.py [--list]
    python3 update_command_cache.py --remove <sha>
    python3 update_command_cache.py --flip <sha> <verdict>

The CCCS_FIRES_ACCESS=1 env var is required to read telemetry.db through the
bash-hard-deny hook's allowlist.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

# Make cccs_hooks / cc_session_tools importable when running from the skill dir.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from cccs_hooks.cache import (  # noqa: E402
    _connect,
    cache_lookup,
    cache_record,
)
from cc_session_tools.lib import telemetry_store  # noqa: E402

_DEFAULT_PREVIEW_LIMIT = 200


def read_telemetry_events(hooks_dir: Path | None = None) -> list[dict[str, object]]:
    conn = telemetry_store.connect(hooks_dir)
    try:
        rows = conn.execute(
            "SELECT * FROM telemetry_events WHERE hook = 'bash-security-review' ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
```

```python
# collect_candidates() body is otherwise unchanged (it already only reads dict-style keys:
# entry.get("hook"), entry.get("verdict"), entry.get("cache"), entry.get("input_hash"),
# entry.get("ts"), entry.get("session_id") — all present on the new dict rows). Drop the
# `if entry.get("hook") != "bash-security-review": continue` filter line inside
# collect_candidates() since read_telemetry_events() now does that filtering in SQL — leaving
# it in would be a harmless no-op, but redundant filtering is exactly the smell
# coding-standards.md warns against ("validate once, trust afterwards").
```

```python
def cmd_list(args: argparse.Namespace) -> int:
    if os.environ.get("CCCS_FIRES_ACCESS") != "1":
        sys.stderr.write(
            "Refusing to read telemetry.db without CCCS_FIRES_ACCESS=1.\n"
            "Re-run as: CCCS_FIRES_ACCESS=1 python3 update_command_cache.py\n"
        )
        return 2
    rows = read_telemetry_events()
    candidates = collect_candidates(rows)
    ...  # rest unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest skills/update-command-cache/tests/ -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add skills/update-command-cache/scripts/update_command_cache.py skills/update-command-cache/tests/ pyproject.toml
git commit -m "fix(skills): read telemetry.db instead of retired fires.jsonl in update-command-cache"
```

---

### Task 13: Migration script — `fires.jsonl*` → `telemetry.db`

**Files:**
- Create: `scripts/migrate_fires_jsonl_to_telemetry_db.py`
- Test: `tests/test_migrate_fires_jsonl_to_telemetry_db.py`

Per overview.md §4: write the new store without touching old files, verify row counts, tar-backup,
only then delete. Backfills the live `fires.jsonl` plus any of `.1/.2/.3` that exist (nothing
older — `_ROTATION_KEEP` was 3, so `.4`+ never existed); this is observability data, not
irreplaceable content, so "everything currently on disk" is the right and sufficient scope.

**Design correction (found by adversarial review) — Migration seam caveat on the `id == N`
cursor alignment.** The Schema section's "Migration nicety" states that the Nth migrated catch-up
row gets `id == N`, matching an old `.cursors/<uuid>.json` `{"offset": N}` value so no cursor
rewrite is needed. That equality holds **only if rotation never discarded old catch-up rows before
this migration runs.** Rotation *does* discard: `_ROTATION_KEEP` was 3, so on a machine that has
rotated at all, the oldest catch-up rows are already gone from disk and cannot be migrated. The
migrated table therefore starts numbering from fewer rows than the historical count some session's
stored `offset` was derived from. That session's `read_since(offset)` will surface nothing until
the ledger grows past its stale offset again — which is exactly the rotation/cursor-desync class
this phase exists to fix, reappearing once at the migration seam. This is an **accepted, bounded,
self-healing limitation of migrating from a lossy source** (the rotation had already dropped the
data pre-migration — the migration neither causes nor worsens the loss): `read_since` *clamps* a
stale offset rather than crashing (proven by `test_read_since_clamps_offset_beyond_current_max_id`
in Task 3), and any affected session recovers on its own as new catch-up rows accumulate, or
immediately if it is re-seeded. Do **not** re-describe this as "no rewrite needed" without this
qualification; it is only unconditionally true on a machine whose `fires.jsonl` never rotated.

**Recovery from a partial run.** The migration is safe to re-run in almost every failure case
(it writes to a fresh table, verifies row counts, and only deletes source files after the
tar-backup succeeds). The one window that needs a manual step is a kill **after** `conn.commit()`
succeeds but **before** the tar-backup + source-delete completes: the dest DB now holds the
inserted rows, so a plain re-run hits the `already has N row(s). Refusing to double-insert` guard
(safe, but blocks progress), while re-running with `--force` would double-insert every row
(AUTOINCREMENT id alignment with the cursor design forbids `INSERT OR IGNORE` dedup-by-content, so
`--force` genuinely duplicates). To recover, truncate the dest tables and reset their id
sequences, then re-run **without** `--force` (the guard now passes because the tables are empty
again):

```bash
sqlite3 ~/.local/share/claude/telemetry.db \
  "DELETE FROM telemetry_events; \
   DELETE FROM catchup_events; \
   DELETE FROM sqlite_sequence WHERE name IN ('telemetry_events','catchup_events');"
python3 scripts/migrate_fires_jsonl_to_telemetry_db.py   # plain re-run; source files still present
```

Resetting `sqlite_sequence` is required so the re-inserted rows recover the `id == N` alignment
above; without it the AUTOINCREMENT high-water mark would resume past N and desync every migrated
cursor. The source `fires.jsonl*` files are still on disk in this window (they are deleted only
*after* the backup step the operator never reached), so nothing is lost.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_migrate_fires_jsonl_to_telemetry_db.py
from __future__ import annotations

import json
import sqlite3
import sys
import tarfile
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import migrate_fires_jsonl_to_telemetry_db as mig  # noqa: E402


def _generic_line(ts: str, hook: str = "bash-security-review") -> str:
    return json.dumps({
        "v": 1, "ts": ts, "hook": hook, "event": "PreToolUse", "tool": "Bash",
        "session_id": "s1", "cwd": "repos/x", "decision": "allow", "cache": "none",
        "verdict": "safe", "input_hash": "sha256:aa",
    })


def _catchup_line(ts: str, job_id: str) -> str:
    verdict = json.dumps({
        "job_id": job_id, "event": "run", "owed": 1, "ran": 1, "exit_code": 0,
        "duration_ms": 5, "error": None, "consecutive_failures": 0,
    })
    return json.dumps({
        "v": 1, "ts": ts, "hook": "catchup", "event": "", "tool": "", "session_id": "",
        "cwd": "", "decision": "annotate", "cache": "none", "verdict": verdict, "input_hash": "",
    })


def test_migrate_splits_generic_and_catchup_rows(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(
        _generic_line("2026-07-01T00:00:00Z") + "\n" + _catchup_line("2026-07-01T00:00:01Z", "tesco") + "\n"
    )
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    assert rc == 0
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    events = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    catchup = conn.execute("SELECT COUNT(*) FROM catchup_events").fetchone()[0]
    conn.close()
    assert events == 1
    assert catchup == 1


def test_migrate_reads_rotated_slots_oldest_first(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl.2").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")
    (source / "fires.jsonl.1").write_text(_generic_line("2026-06-15T00:00:00Z") + "\n")
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT ts FROM telemetry_events ORDER BY id").fetchall()
    conn.close()
    assert [r["ts"] for r in rows] == [
        "2026-06-01T00:00:00Z", "2026-06-15T00:00:00Z", "2026-07-01T00:00:00Z",
    ]


def test_migrate_skips_malformed_lines_without_failing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text("not json\n" + _generic_line("2026-07-01T00:00:00Z") + "\n")
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    assert rc == 0
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    events = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert events == 1


def test_migrate_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=True, force=False)
    assert rc == 0
    assert not (dest / "telemetry.db").exists()
    assert (source / "fires.jsonl").exists()


def test_migrate_backs_up_then_deletes_source_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    (source / "fires.jsonl.1").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    assert not (source / "fires.jsonl").exists()
    assert not (source / "fires.jsonl.1").exists()
    backups = list((dest / "migration-backups").glob("fires-jsonl-*.tar.gz"))
    assert len(backups) == 1
    with tarfile.open(backups[0]) as tar:
        names = set(tar.getnames())
    assert names == {"fires.jsonl", "fires.jsonl.1"}


def test_migrate_refuses_to_double_insert_without_force(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    # Re-run against the same (now-empty, already-migrated) source: nothing to do.
    source.mkdir(exist_ok=True)
    (source / "fires.jsonl").write_text(_generic_line("2026-07-02T00:00:00Z") + "\n")
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    assert rc == 1
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    events = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert events == 1  # the second run's row was NOT inserted


def test_migrate_no_source_files_is_a_no_op(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False, force=False)
    assert rc == 0
    assert not (dest / "telemetry.db").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_migrate_fires_jsonl_to_telemetry_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'migrate_fires_jsonl_to_telemetry_db'`

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
# scripts/migrate_fires_jsonl_to_telemetry_db.py
"""One-shot migration: fires.jsonl (+ rotated .1/.2/.3 slots) → telemetry.db.

Reads ~/.cache/claude/logs/fires.jsonl and any of fires.jsonl.{1,2,3} that
exist (oldest slot first: .3, .2, .1, then the live file — so rows land in
telemetry.db in original chronological order), classifies each line as a
generic telemetry_events row or a catchup_events row (hook == "catchup"),
and inserts them into telemetry.db under the new data-home root.

Because catchup_events.id is INTEGER PRIMARY KEY AUTOINCREMENT and rows are
inserted in original chronological order into an initially-empty table, the
Nth catch-up row inserted gets id == N — the same integer the existing
row-count-based cursor files (<scheduler-dir>/.cursors/<uuid>.json,
{"offset": N}) already store. On a machine whose fires.jsonl never rotated,
no cursor-file rewrite is needed: an old stored offset of 42 continues to
mean "the 42nd catch-up row" post-migration. If rotation already discarded
old catch-up rows before this runs, the alignment is off by the number of
dropped rows — a bounded, self-healing limitation of migrating from a lossy
source (read_since clamps a stale offset rather than crashing). See the
"Migration seam caveat" in this task's plan notes.

Non-destructive: writes to telemetry.db, verifies the inserted row count
against the parsed row count, tar.gz-backs-up the source fires.jsonl* files
to <dest-dir>/migration-backups/, and only then deletes them from the source
directory. Malformed lines are skipped and counted, never silently dropped
from the summary — this is observability data, not irreplaceable content
(see docs/superpowers/plans/2026-07-13-data-store-uplift-00-overview.md §4).

Usage:
    python3 scripts/migrate_fires_jsonl_to_telemetry_db.py [--dry-run] [--force]
    python3 scripts/migrate_fires_jsonl_to_telemetry_db.py \
        --source-dir ~/.cache/claude/logs --dest-dir ~/.local/share/claude

Run this manually, once per machine, after Phase 5 has been deployed —
not part of `ccst install` (see design-spec §8.3/§8.5).
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from cc_session_tools.lib import paths, telemetry_store  # noqa: E402

_OLD_DEFAULT_SOURCE_DIR = Path.home() / ".cache" / "claude" / "logs"
_ROTATED_SLOTS_OLDEST_FIRST = (3, 2, 1)


def _source_files(source_dir: Path) -> list[Path]:
    files = [
        source_dir / f"fires.jsonl.{n}"
        for n in _ROTATED_SLOTS_OLDEST_FIRST
        if (source_dir / f"fires.jsonl.{n}").is_file()
    ]
    live = source_dir / "fires.jsonl"
    if live.is_file():
        files.append(live)
    return files


def _parse_lines(files: list[Path]) -> tuple[list[dict[str, object]], list[dict[str, object]], int]:
    """Returns (telemetry_rows, catchup_rows, malformed_count)."""
    telemetry_rows: list[dict[str, object]] = []
    catchup_rows: list[dict[str, object]] = []
    malformed = 0
    for f in files:
        for raw in f.read_text().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(line, dict):
                malformed += 1
                continue
            if line.get("hook") == "catchup":
                try:
                    detail = json.loads(str(line.get("verdict", "{}")))
                except json.JSONDecodeError:
                    detail = {}
                catchup_rows.append({
                    "ts": line.get("ts", ""),
                    "job_id": str(detail.get("job_id", "")),
                    "event": str(detail.get("event", "")),
                    "owed": int(detail.get("owed", 0) or 0),
                    "ran": int(detail.get("ran", 0) or 0),
                    "exit_code": detail.get("exit_code"),
                    "duration_ms": int(detail.get("duration_ms", 0) or 0),
                    "error": detail.get("error"),
                    "consecutive_failures": int(detail.get("consecutive_failures", 0) or 0),
                })
            else:
                telemetry_rows.append({
                    "ts": line.get("ts", ""),
                    "hook": str(line.get("hook", "")),
                    "event": str(line.get("event", "")),
                    "tool": str(line.get("tool", "")),
                    "session_id": str(line.get("session_id", "")),
                    "cwd_short": str(line.get("cwd", "")),
                    "decision": str(line.get("decision", "")),
                    "cache": str(line.get("cache", "")),
                    "verdict": str(line.get("verdict", "")),
                    "input_hash": str(line.get("input_hash", "")),
                })
    return telemetry_rows, catchup_rows, malformed


def migrate(*, source_dir: Path, dest_dir: Path, dry_run: bool, force: bool) -> int:
    files = _source_files(source_dir)
    if not files:
        print(f"No fires.jsonl* files found under {source_dir} — nothing to migrate.")
        return 0

    telemetry_rows, catchup_rows, malformed = _parse_lines(files)
    print(
        f"Parsed {len(files)} file(s): {len(telemetry_rows)} telemetry row(s), "
        f"{len(catchup_rows)} catchup row(s), {malformed} malformed line(s) skipped."
    )

    dest_db = dest_dir / telemetry_store.DB_FILENAME
    if dry_run:
        print(f"[dry-run] would insert into {dest_db}")
        return 0

    conn = telemetry_store.connect(dest_dir)
    try:
        before_t = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
        before_c = conn.execute("SELECT COUNT(*) FROM catchup_events").fetchone()[0]
        if (before_t + before_c) > 0 and not force:
            print(
                f"ERROR: {dest_db} already has {before_t + before_c} row(s). Refusing to "
                "double-insert. Re-run with --force if this is intentional.",
                file=sys.stderr,
            )
            return 1

        for r in telemetry_rows:
            conn.execute(
                "INSERT INTO telemetry_events "
                "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
                "VALUES (:ts, :hook, :event, :tool, :session_id, :cwd_short, :decision, :cache, :verdict, :input_hash)",
                r,
            )
        for r in catchup_rows:
            conn.execute(
                "INSERT INTO catchup_events "
                "(ts, job_id, event, owed, ran, exit_code, duration_ms, error, consecutive_failures) "
                "VALUES (:ts, :job_id, :event, :owed, :ran, :exit_code, :duration_ms, :error, :consecutive_failures)",
                r,
            )
        conn.commit()

        after_t = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
        after_c = conn.execute("SELECT COUNT(*) FROM catchup_events").fetchone()[0]
    finally:
        conn.close()

    if after_t - before_t != len(telemetry_rows) or after_c - before_c != len(catchup_rows):
        print(
            "ERROR: verification failed — inserted row count does not match parsed "
            f"row count (telemetry: {after_t - before_t} vs {len(telemetry_rows)}, "
            f"catchup: {after_c - before_c} vs {len(catchup_rows)}). Source files left "
            "untouched.",
            file=sys.stderr,
        )
        return 1

    backup_dir = dest_dir / "migration-backups"
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"fires-jsonl-{stamp}.tar.gz"
    with tarfile.open(backup_path, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=f.name)
    print(f"Backed up {len(files)} source file(s) to {backup_path}")

    for f in files:
        f.unlink()
    print(f"Removed {len(files)} source file(s) from {source_dir}")

    print(
        f"Migration complete: {len(telemetry_rows)} telemetry row(s), "
        f"{len(catchup_rows)} catchup row(s) inserted into {dest_db}. "
        f"{malformed} malformed line(s) skipped."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--source-dir", default=None, metavar="PATH",
        help=f"Old JSONL directory (default: {_OLD_DEFAULT_SOURCE_DIR})",
    )
    p.add_argument(
        "--dest-dir", default=None, metavar="PATH",
        help="New telemetry.db directory (default: paths.data_home())",
    )
    p.add_argument("--dry-run", action="store_true", help="Report what would be migrated without writing anything")
    p.add_argument("--force", action="store_true", help="Allow inserting into a dest DB that already has rows")
    args = p.parse_args(argv)

    source_dir = Path(args.source_dir) if args.source_dir else _OLD_DEFAULT_SOURCE_DIR
    dest_dir = Path(args.dest_dir) if args.dest_dir else paths.data_home()

    print(f"Source: {source_dir}")
    print(f"Dest  : {dest_dir / telemetry_store.DB_FILENAME}")

    return migrate(source_dir=source_dir, dest_dir=dest_dir, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_migrate_fires_jsonl_to_telemetry_db.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_fires_jsonl_to_telemetry_db.py tests/test_migrate_fires_jsonl_to_telemetry_db.py
git commit -m "feat(migration): add fires.jsonl → telemetry.db one-shot migration script"
```

- [ ] **Step 6: Note for the human running this on their own machine (not part of the automated plan)**

After this phase's PR merges, run once, for real, with no flags (writes to the real
`~/.cache/claude/logs` source and the real `paths.data_home()` dest):

```bash
python3 scripts/migrate_fires_jsonl_to_telemetry_db.py --dry-run   # inspect first
python3 scripts/migrate_fires_jsonl_to_telemetry_db.py             # then apply
```

---

### Task 14: Full-suite verification

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest -q
```

Expected: all tests pass, with no unexplained changes outside the files this plan touched. In
particular:
- `tests/scheduler/test_reconcile.py`, `tests/scheduler/test_worker.py`,
  `tests/scheduler/test_cursor.py` (if present) — untouched by this plan, must still pass
  unmodified, proving `ledger.record`/`current_offset`'s call-signature stability held.
- `tests/scheduler/test_ccsched_cli.py::test_status_empty_ok`, `test_sweep_runs` — untouched
  assertions, must still pass (only two assertions in this file were touched, per Task 7).

- [ ] **Step 2: Confirm the zero-source-change claim for the untouched modules**

```bash
git diff --stat main -- src/cc_session_tools/lib/scheduler/cursor.py \
    src/cc_session_tools/lib/scheduler/surface.py \
    src/cc_session_tools/lib/scheduler/reconcile.py \
    src/cc_session_tools/lib/scheduler/worker.py \
    src/cc_session_tools/cli/ccsched.py \
    src/cccs_hooks/catchup.py
```

Expected: empty output (no diff) for every file in this list — confirms the "keep
`ledger.record`/`read_since`/`current_offset`'s existing call signatures" design goal from the
task brief held in practice, not just in intent.

- [ ] **Step 3: Grep for any remaining `fires.jsonl` references outside historical/comment context**

```bash
grep -rn "fires\.jsonl" src/ skills/ scripts/ | grep -v "\.pyc"
```

Expected: zero hits, or only hits inside comments that explicitly describe the *retired* format
for historical context (e.g. a "this used to be..." note is disallowed by this repo's
coding-standards.md — if any non-comment code reference remains, it is a bug in this plan's
execution and must be fixed before proceeding).

- [ ] **Step 4: Optional type-check (not CI-gated in this repo, but `mypy` is a configured dev dependency)**

```bash
uv run mypy src/cc_session_tools/lib/telemetry_store.py \
    src/cccs_hooks/telemetry.py \
    src/cccs_hooks/telemetry_trim.py \
    src/cccs_hooks/telemetry_query.py \
    src/cc_session_tools/lib/scheduler/ledger.py \
    scripts/migrate_fires_jsonl_to_telemetry_db.py
```

- [ ] **Step 5: Manual smoke test of the new CLI commands**

```bash
export CCCS_HOOKS_DIR=/tmp/telemetry-smoke-test
mkdir -p "$CCCS_HOOKS_DIR"
echo '{"session_id":"s1","cwd":"/tmp/x","tool_name":"Bash"}' | \
    uv run python -m cccs_hooks.telemetry log --hook smoke-test --event PreToolUse \
    --decision allow --cache miss --verdict safe --input-hash sha256:00
uv run python -m cc_session_tools.cli.ccst telemetry query --hooks-dir "$CCCS_HOOKS_DIR"
uv run python -m cc_session_tools.cli.ccst telemetry trim --hooks-dir "$CCCS_HOOKS_DIR" --max-age-days 0
uv run python -m cc_session_tools.cli.ccst telemetry query --hooks-dir "$CCCS_HOOKS_DIR"
rm -rf /tmp/telemetry-smoke-test
```

Expected: the first `query` shows the smoke-test row; `trim --max-age-days 0` deletes it (cutoff
is "now", and the row's `ts` is at or before now); the second `query` prints "No matching
telemetry events."

---

## Handoff

Phase 5 is complete when: `telemetry.db` is the live store for both hook-fire and catch-up events,
`ccst telemetry query` and the rewritten `ccst telemetry trim` are shipped, the id-based cursor fix
has a passing regression test reproducing the old desync bug's shape, the migration script has run
successfully against this developer's own machine (Task 13, Step 6 — a manual, one-off action, not
part of the automated task list), and Task 14's full-suite/grep/manual-smoke checks are clean. This
does not touch version bump or `CHANGELOG.md` — that is Phase 7's job (per
`2026-07-13-data-store-uplift-00-overview.md`, Phase 7 is the integration/cleanup pass and must go
last across all of Phases 2-6).
