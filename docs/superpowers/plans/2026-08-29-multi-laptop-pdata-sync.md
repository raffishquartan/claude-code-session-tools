# Multi-laptop `ccst pdata` sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `ccst pdata`'s 10 `~/cc`-rooted projects stay in sync across Chris's two laptops
automatically - an N-machine vector clock detects clean catch-up vs. genuine conflict, dumps/
rehydrates ride OneDrive's existing sync of the project folder, and a conflict is always
surfaced to Chris, never silently resolved.

**Architecture:** A new `pdata_meta` table (the vector clock) lives inside every in-scope
project's `.db`. A thin, explicitly-ordered dump routine (not raw `iterdump()`) serialises a
project's full content + vector to `.pdata-db-dump/latest.sql` + `.sha256`. SessionStart,
SessionEnd, and an hourly bundled `ccsched` job each run a narrow compare-and-act routine against
that dump; three new `ccst pdata` CLI commands (`dump`, `rehydrate`, `resolve`) expose the same
logic manually. A project-occupancy check (live `claude` processes, by cwd) and a per-DB
non-blocking lock check gate the risky content-replacing direction.

**Tech Stack:** Python 3.11+, stdlib `sqlite3` (no `sqlite3` CLI binary - confirmed absent on this
machine), existing `cc_session_tools.lib.db`/`lib.pdata.*` modules, `ccsched` for the hourly job,
Claude Code's `SessionStart`/`SessionEnd` hook events.

**Spec:** `docs/superpowers/specs/2026-08-29-multi-laptop-pdata-sync-design.md` (read this first -
this plan does not repeat its rationale, only what to build).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cc_session_tools/lib/pdata/vector_clock.py` (new) | `pdata_meta` DDL; read/bump-own/merge-in/compare(dominates, dominated, fork) on a plain `dict[str, int]` vector - no I/O, no SQLite connection held beyond what's passed in |
| `src/cc_session_tools/lib/pdata/dump.py` (new) | Deterministic serialisation of one project's `.db` to SQL text (custom `ORDER BY`-per-table wrapper, not `iterdump()`); checksum; write/read `.pdata-db-dump/{latest.sql,latest.sha256}`; archive rotation (keep 24) |
| `src/cc_session_tools/lib/pdata/rehydrate.py` (new) | Checksum-validate a dump, compare its vector against the local DB's, and apply a clean fast-forward via temp-file-then-atomic-rename; never touches a dump found to conflict (returns a result type the caller reports on) |
| `src/cc_session_tools/lib/pdata/sync_lock.py` (new) | The per-DB non-blocking `BEGIN IMMEDIATE` exclusive check (rehydrate's write-safety gate) |
| `src/cc_session_tools/lib/occupancy.py` (new) | "Is a live `claude` process's cwd exactly this project root?" - `pgrep` + `/proc/<pid>/cwd` (Linux/WSL2) or `lsof -a -p <pid> -d cwd -Fn` (macOS); excludes a given PID (SessionStart's own) |
| `src/cc_session_tools/lib/machine_identity.py` (new) | Resolve/store this laptop's `machine_id`; the explicit one-time confirm CLI path; the per-project collision check against a vector |
| `src/cc_session_tools/lib/pdata/repository.py` (modify) | Add `pdata_meta` to the DDL applied on every connect; nothing else - revision-bumping is a service-layer concern (see below), not repository's |
| `src/cc_session_tools/lib/pdata/service.py` (modify) | `add_record`/`update_record`/`delete_record`/`restore_record`/`schema_add_field` each bump the local machine's own `pdata_meta` revision inside their existing transaction |
| `src/cc_session_tools/lib/pdata/init_service.py`, `init_paths.py` (modify) | Adopt-from-dump fast path: if a valid dump exists and no local `.db` does, rehydrate instead of classify/import |
| `src/cc_session_tools/lib/pdata/resolve.py` (new) | Per-record diff (local DB vs. dump) + interactive resolution (reusing display conventions from `pm-pdata-conflict-resolution`'s existing exit-3 diff) + the post-resolve vector-clock bookkeeping from the spec |
| `src/cccs_hooks/pdata_sync.py` (new) | `SessionStart`/`SessionEnd` hook entry points - thin wrappers calling `rehydrate`/`dump`, occupancy-gated |
| `src/cc_session_tools/lib/scheduler/bundled_jobs.py` (modify) | New bundled job, hourly, running both checks per project |
| `src/cc_session_tools/cli/ccst.py` (modify) | New `ccst pdata dump/rehydrate/resolve` subcommands; new `ccst machine-identity show/confirm` subcommand |
| `src/cc_session_tools/skills/pm-pdata-conflict-resolution/SKILL.md` (modify) | New section for the cross-machine fork case, alongside the existing single-file case |

**Plan decision, not in the spec - settle now rather than discover it mid-implementation:**
machine-identity confirmation is an **explicit, separate CLI command** (`ccst machine-identity
confirm`), never something a hook blocks on. A hook (SessionStart, SessionEnd, the cron job) has
no interactive tty to prompt against. If `machine_identity.resolve()` finds no stored value yet,
it returns the raw hostname as a **provisional** identity and the caller logs one line noting it's
unconfirmed and naming the command to fix that - it never blocks sync on confirmation.

---

## Task 1: Vector clock primitives (no I/O)

**Files:**
- Create: `src/cc_session_tools/lib/pdata/vector_clock.py`
- Test: `tests/pdata/test_vector_clock.py`

- [ ] **Step 1: Write the failing tests**

```python
from cc_session_tools.lib.pdata.vector_clock import Comparison, bump_own, compare, merge


def test_compare_dominates_when_strictly_ahead_everywhere_or_equal():
    local = {"a": 1, "b": 2}
    dump = {"a": 1, "b": 3}
    assert compare(local=local, dump=dump) == Comparison.DUMP_DOMINATES


def test_compare_dominated_when_local_strictly_ahead():
    local = {"a": 1, "b": 3}
    dump = {"a": 1, "b": 2}
    assert compare(local=local, dump=dump) == Comparison.LOCAL_DOMINATES


def test_compare_equal_is_local_dominates_not_a_fork():
    # Equal vectors mean nothing to do - treated as LOCAL_DOMINATES (a no-op, not DUMP_DOMINATES
    # which would trigger a pointless rehydrate, and not FORK which would wrongly block).
    v = {"a": 1, "b": 2}
    assert compare(local=v, dump=dict(v)) == Comparison.LOCAL_DOMINATES


def test_compare_fork_when_each_side_has_something_the_other_lacks():
    local = {"a": 2, "b": 1}
    dump = {"a": 1, "b": 2}
    assert compare(local=local, dump=dump) == Comparison.FORK


def test_compare_handles_machine_known_to_only_one_side():
    # dump knows about "c", local never has - missing entries default to 0.
    local = {"a": 1}
    dump = {"a": 1, "c": 1}
    assert compare(local=local, dump=dump) == Comparison.DUMP_DOMINATES


def test_bump_own_increments_only_the_named_machine():
    v = {"ltxy": 3, "macbook": 5}
    bump_own(v, "ltxy")
    assert v == {"ltxy": 4, "macbook": 5}


def test_bump_own_creates_the_row_for_a_brand_new_machine():
    v: dict[str, int] = {}
    bump_own(v, "ltxy")
    assert v == {"ltxy": 1}


def test_merge_takes_elementwise_max():
    a = {"x": 1, "y": 5}
    b = {"x": 3, "y": 2, "z": 1}
    assert merge(a, b) == {"x": 3, "y": 5, "z": 1}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/pdata/test_vector_clock.py -v`
Expected: FAIL - `ModuleNotFoundError` (module doesn't exist yet).

- [ ] **Step 3: Implement**

```python
"""Pure vector-clock comparison/merge for ccst pdata's cross-machine sync (spec: "The vector
clock"). No I/O, no SQLite - operates on a plain {machine_id: revision} mapping so it's trivially
unit-testable; vector_clock_store.py (Task 2) is the only module that touches pdata_meta rows."""
from __future__ import annotations

import enum


class Comparison(enum.Enum):
    LOCAL_DOMINATES = "local_dominates"  # dump is stale/already-incorporated, or equal - no-op
    DUMP_DOMINATES = "dump_dominates"     # clean fast-forward: safe to rehydrate
    FORK = "fork"                        # each side has a revision the other lacks


def compare(*, local: dict[str, int], dump: dict[str, int]) -> Comparison:
    """Spec's "Comparison rule". Missing entries on either side default to 0 - a machine neither
    vector has ever heard of contributes nothing either way."""
    keys = set(local) | set(dump)
    local_ahead = any(local.get(k, 0) > dump.get(k, 0) for k in keys)
    dump_ahead = any(dump.get(k, 0) > local.get(k, 0) for k in keys)
    if local_ahead and dump_ahead:
        return Comparison.FORK
    if dump_ahead:
        return Comparison.DUMP_DOMINATES
    return Comparison.LOCAL_DOMINATES  # local strictly ahead, or exactly equal


def bump_own(vector: dict[str, int], machine_id: str) -> None:
    """Mutates vector in place - the one call every local pdata write makes, in the same
    transaction as the data change (binding invariant #1)."""
    vector[machine_id] = vector.get(machine_id, 0) + 1


def merge(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    """Elementwise max, union of keys. Used on a clean fast-forward (adopt the dump's vector,
    which already dominates) and after a manual resolve (adopt the other side's revision,
    max every other machine)."""
    keys = set(a) | set(b)
    return {k: max(a.get(k, 0), b.get(k, 0)) for k in keys}
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/pdata/test_vector_clock.py -v`
Expected: 8 passed.

- [ ] **Step 5: `mypy --strict` the new module**

Run: `mypy --strict src/cc_session_tools/lib/pdata/vector_clock.py`
Expected: `Success: no issues found in 1 source file`

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/lib/pdata/vector_clock.py tests/pdata/test_vector_clock.py
git commit -m "pdata sync: add pure vector-clock comparison/merge primitives"
```

---

## Task 2: `pdata_meta` table + read/write against a real DB

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/repository.py:30-47` (the `_BASE_DDL` string)
- Create: `src/cc_session_tools/lib/pdata/vector_clock_store.py`
- Test: `tests/pdata/test_vector_clock_store.py`

- [ ] **Step 1: Write the failing tests**

```python
from cc_session_tools.lib.pdata import repository, vector_clock_store


def test_read_vector_is_empty_on_a_fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("proj")
    assert vector_clock_store.read_vector(conn) == {}


def test_bump_own_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("proj")
    with repository._immediate(conn):
        vector_clock_store.bump_own(conn, "ltxy")
        vector_clock_store.bump_own(conn, "ltxy")
    assert vector_clock_store.read_vector(conn) == {"ltxy": 2}


def test_write_vector_overwrites_every_row(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("proj")
    with repository._immediate(conn):
        vector_clock_store.write_vector(conn, {"ltxy": 3, "macbook": 1}, updated_at=100)
    assert vector_clock_store.read_vector(conn) == {"ltxy": 3, "macbook": 1}
    with repository._immediate(conn):
        vector_clock_store.write_vector(conn, {"ltxy": 4}, updated_at=200)
    # macbook's row must be gone - write_vector replaces the whole table, it doesn't merge.
    assert vector_clock_store.read_vector(conn) == {"ltxy": 4}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/pdata/test_vector_clock_store.py -v`
Expected: FAIL - `ModuleNotFoundError`.

- [ ] **Step 3: Add the table to `_BASE_DDL`**

In `repository.py`, append to the existing `_BASE_DDL` string (after `record_group_fields`'s
closing `"""` — keep it inside the same triple-quoted block):

```sql
CREATE TABLE IF NOT EXISTS pdata_meta (
    machine_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
```

- [ ] **Step 4: Implement `vector_clock_store.py`**

```python
"""Reads/writes the pdata_meta table (spec: "The vector clock") for one already-open connection.
Pure vector math lives in vector_clock.py - this module is the only place that touches SQL for it.
Caller owns the transaction (wrap writes in repository._immediate), matching every other write
path in this package."""
from __future__ import annotations

import sqlite3

from cc_session_tools.lib.pdata import vector_clock


def read_vector(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT machine_id, revision FROM pdata_meta").fetchall()
    return {row["machine_id"]: row["revision"] for row in rows}


def write_vector(conn: sqlite3.Connection, vector: dict[str, int], *, updated_at: int) -> None:
    """Replaces the entire table's contents - the caller already has the full merged/bumped
    vector it wants in hand (vector_clock.merge()/bump_own() are pure dict operations), so this
    is always a full overwrite, never a partial update."""
    conn.execute("DELETE FROM pdata_meta")
    conn.executemany(
        "INSERT INTO pdata_meta (machine_id, revision, updated_at) VALUES (?, ?, ?)",
        [(machine_id, revision, updated_at) for machine_id, revision in vector.items()],
    )


def bump_own(conn: sqlite3.Connection, machine_id: str, *, updated_at: int | None = None) -> None:
    """Convenience used by every service.py write path: read, bump, write, all inside the
    caller's already-open transaction."""
    import time

    v = read_vector(conn)
    vector_clock.bump_own(v, machine_id)
    write_vector(conn, v, updated_at=updated_at if updated_at is not None else int(time.time()))
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/pdata/test_vector_clock_store.py -v`
Expected: 3 passed.

- [ ] **Step 6: `mypy --strict`, then commit**

```bash
mypy --strict src/cc_session_tools/lib/pdata/vector_clock_store.py
git add src/cc_session_tools/lib/pdata/repository.py src/cc_session_tools/lib/pdata/vector_clock_store.py tests/pdata/test_vector_clock_store.py
git commit -m "pdata sync: add pdata_meta table and its read/write helpers"
```

---

## Task 3: Wire the revision bump into every write path

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/service.py` (`add_record:84`, `update_record:290`,
  `delete_record:378`, `restore_record:409`, `schema_add_field:422` - line numbers as of this
  plan's writing, re-check before editing)
- Test: `tests/pdata/test_service.py` (extend existing file - do not create a new one)

- [ ] **Step 1: Write the failing tests** (add to the existing test file; read it first to match
  its existing fixture conventions rather than inventing new ones)

```python
def test_add_record_bumps_own_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv("CCST_MACHINE_NAME", "ltxy")
    service.add_record(project="proj", record_group="g", content="x")
    conn = repository.connect("proj")
    assert vector_clock_store.read_vector(conn) == {"ltxy": 1}


def test_two_writes_bump_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv("CCST_MACHINE_NAME", "ltxy")
    service.add_record(project="proj", record_group="g", content="x")
    service.add_record(project="proj", record_group="g", content="y")
    conn = repository.connect("proj")
    assert vector_clock_store.read_vector(conn) == {"ltxy": 2}


def test_update_delete_restore_and_schema_add_field_each_bump_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv("CCST_MACHINE_NAME", "ltxy")
    rec = service.add_record(project="proj", record_group="g", content="x")
    service.update_record(project="proj", record_id=rec.id, version=rec.version, content="y")
    service.delete_record(project="proj", record_id=rec.id)
    service.restore_record(project="proj", record_id=rec.id)
    service.schema_add_field(project="proj", record_group="g", field_name="f", column_type="TEXT")
    conn = repository.connect("proj")
    # 1 (add) + 1 (update) + 1 (delete) + 1 (restore) + 1 (schema_add_field) = 5
    assert vector_clock_store.read_vector(conn) == {"ltxy": 5}
```

Each test's exact call signature must match `service.py`'s real function signatures - read them
first (`add_record:84`, etc.) rather than guessing keyword names.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/pdata/test_service.py -k "bump" -v`
Expected: FAIL - `pdata_meta` stays empty (the bump call doesn't exist yet).

- [ ] **Step 3: Implement** - in each of the five functions, inside the existing
  `with repository._immediate(conn):` block (every one of them already opens one - confirm this
  while editing, don't add a second transaction), add one line:

```python
    from cc_session_tools.lib.pdata import vector_clock_store
    from cc_session_tools.lib.machine_identity import resolve as resolve_machine_id
    ...
    vector_clock_store.bump_own(conn, resolve_machine_id().machine_id)
```

(`resolve_machine_id()` is Task 6's `machine_identity.resolve()` - this task has a hard dependency
on Task 6 landing first; do Task 6 before this one if working through tasks out of order.)

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/pdata/test_service.py -v`
Expected: all pass, including the 3 new ones.

- [ ] **Step 5: Full suite + mypy, then commit**

```bash
pytest -q
mypy --strict src/cc_session_tools/lib/pdata/service.py
git add src/cc_session_tools/lib/pdata/service.py tests/pdata/test_service.py
git commit -m "pdata sync: bump local machine's revision on every write"
```

---

## Task 4: Machine identity

**Files:**
- Create: `src/cc_session_tools/lib/machine_identity.py`
- Modify: `src/cc_session_tools/cli/ccst.py` (new `machine-identity show|confirm` subcommand)
- Test: `tests/test_machine_identity.py`, `tests/test_ccst_machine_identity_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_machine_identity.py
import socket

from cc_session_tools.lib import machine_identity


def test_resolve_uses_stored_value_if_present(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_MACHINE_NAME", "ltxy")
    result = machine_identity.resolve()
    assert result.machine_id == "ltxy"
    assert result.confirmed is True


def test_resolve_falls_back_to_hostname_unconfirmed(monkeypatch):
    monkeypatch.delenv("CCST_MACHINE_NAME", raising=False)
    result = machine_identity.resolve()
    assert result.machine_id == socket.gethostname()
    assert result.confirmed is False


def test_check_collision_flags_a_different_known_machine():
    vector = {"macbook": 3}
    assert machine_identity.check_collision(proposed="ltxy", known_vector=vector) is False
    assert machine_identity.check_collision(proposed="macbook", known_vector=vector) is True


def test_check_collision_is_fine_with_a_name_already_recorded_as_itself():
    vector = {"ltxy": 5}
    assert machine_identity.check_collision(proposed="ltxy", known_vector=vector) is False
```

```python
# tests/test_ccst_machine_identity_cli.py - subprocess-based, matching
# tests/test_ccst_pdata_reconcile_cli.py's _run()/base_env pattern exactly
def test_confirm_writes_the_stored_value(base_env, tmp_path):
    ...  # ccst machine-identity confirm --name ltxy --store <tmp_path>/machine-identity.json
    ...  # then: ccst machine-identity show prints "ltxy (confirmed)"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_machine_identity.py tests/test_ccst_machine_identity_cli.py -v`
Expected: FAIL - `ModuleNotFoundError`.

- [ ] **Step 3: Implement `machine_identity.py`**

```python
"""This laptop's identity for ccst pdata's cross-machine vector clock (spec: "Machine identity").

Confirmation is an explicit, separate CLI command (ccst machine-identity confirm) - never
something a hook blocks on, since a SessionStart/SessionEnd/cron hook has no interactive tty.
resolve() always returns *something* usable; .confirmed tells the caller whether it's trustworthy
enough to silence the "unconfirmed" note."""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib import paths

MACHINE_NAME_ENV = "CCST_MACHINE_NAME"


@dataclass(frozen=True, slots=True)
class MachineIdentity:
    machine_id: str
    confirmed: bool


def _store_path() -> Path:
    return paths.data_home() / "machine-identity.json"


def resolve() -> MachineIdentity:
    env = os.environ.get(MACHINE_NAME_ENV)
    if env:
        return MachineIdentity(machine_id=env, confirmed=True)
    store = _store_path()
    if store.exists():
        data = json.loads(store.read_text())
        return MachineIdentity(machine_id=data["machine_id"], confirmed=True)
    return MachineIdentity(machine_id=socket.gethostname(), confirmed=False)


def confirm(name: str) -> None:
    store = _store_path()
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({"machine_id": name}))


def check_collision(*, proposed: str, known_vector: dict[str, int]) -> bool:
    """True iff `proposed` would collide with a *different* machine already known to this
    project's vector - i.e. the vector has any entry other than `proposed` itself. A vector
    containing only `proposed` (or being empty) is not a collision - that's either this same
    machine continuing, or a brand-new project nobody has touched yet."""
    others = set(known_vector) - {proposed}
    return len(others) > 0
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_machine_identity.py -v`
Expected: 4 passed (CLI test still failing - next step).

- [ ] **Step 5: Wire the CLI subcommand in `ccst.py`** (`machine-identity show|confirm --name
  NAME`), matching the existing subparser-per-verb pattern used for `pdata`'s own verbs
  (`pdata_sub.add_parser(...)` at `ccst.py:2321` onward is the pattern to copy, at the top
  level instead of nested under `pdata`).

- [ ] **Step 6: Run to verify pass**

Run: `pytest tests/test_machine_identity.py tests/test_ccst_machine_identity_cli.py -v`
Expected: all pass.

- [ ] **Step 7: `mypy --strict`, then commit**

```bash
mypy --strict src/cc_session_tools/lib/machine_identity.py
git add src/cc_session_tools/lib/machine_identity.py src/cc_session_tools/cli/ccst.py tests/test_machine_identity.py tests/test_ccst_machine_identity_cli.py
git commit -m "pdata sync: machine identity resolve/confirm + per-project collision check"
```

---

## Task 5: Deterministic dump routine (the empirically-tested part)

**Files:**
- Create: `src/cc_session_tools/lib/pdata/dump.py`
- Test: `tests/pdata/test_dump.py`

**Read first:** the spec's "Dump format" section - this task implements exactly the custom
wrapper it specifies, for the exact reason (raw `iterdump()` is not deterministic for a
composite-primary-key table, confirmed empirically during brainstorming).

- [ ] **Step 1: Write the failing tests** - this is the most important test in the whole plan;
  it's the actual proof the design leans on, not a docstring claim:

```python
def test_dump_is_identical_regardless_of_insertion_or_schema_evolution_order(tmp_path):
    """The exact scenario that broke raw iterdump() during design: two databases holding
    identical logical content, built via a DIFFERENT sequence of operations, must still
    produce byte-identical dumps."""
    con_a = _build_db(tmp_path / "a.db", field_order=["owner", "priority"], row_order=[1, 2, 3])
    con_b = _build_db(tmp_path / "b.db", field_order=["priority", "owner"], row_order=[3, 1, 2])
    assert dump.serialize(con_a) == dump.serialize(con_b)


def test_dump_has_no_pragma_or_file_level_settings(tmp_path):
    con = _build_db(tmp_path / "a.db", field_order=["owner"], row_order=[1])
    text = dump.serialize(con)
    assert "PRAGMA" not in text


def test_write_and_read_latest_roundtrip(tmp_path):
    con = _build_db(tmp_path / "a.db", field_order=["owner"], row_order=[1])
    project_root = tmp_path / "proj"
    dump.write_latest(con, project_root=project_root, machine_id="ltxy", vector={"ltxy": 1})
    result = dump.read_latest(project_root)
    assert result.checksum_valid is True
    assert result.vector == {"ltxy": 1}
    assert result.machine_id == "ltxy"


def test_read_latest_detects_a_corrupted_dump(tmp_path):
    con = _build_db(tmp_path / "a.db", field_order=["owner"], row_order=[1])
    project_root = tmp_path / "proj"
    dump.write_latest(con, project_root=project_root, machine_id="ltxy", vector={"ltxy": 1})
    (project_root / ".pdata-db-dump" / "latest.sql").write_text("TRUNCATED")
    result = dump.read_latest(project_root)
    assert result.checksum_valid is False


def test_archive_keeps_only_24_most_recent(tmp_path):
    con = _build_db(tmp_path / "a.db", field_order=["owner"], row_order=[1])
    project_root = tmp_path / "proj"
    for i in range(30):
        dump.write_latest(con, project_root=project_root, machine_id="ltxy", vector={"ltxy": i})
    archived = list((project_root / ".pdata-db-dump" / "archive").glob("*.sql"))
    assert len(archived) == 24
```

(`_build_db` is a test helper creating a fresh db, adding extension fields and rows in the given
orders, and inserting a `pdata_meta` row - write it once at the top of the test file, matching
this package's existing test-helper conventions; read `tests/pdata/test_repository.py` for the
established pattern before writing a new one.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/pdata/test_dump.py -v`
Expected: FAIL - `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Deterministic text serialisation of one project's .db (spec: "Dump format"). NOT
sqlite3.Connection.iterdump() used as-is - empirically confirmed during design that a table with
a composite/non-integer primary key (record_group_fields) dumps in insertion order under
iterdump(), not key order, making two logically-identical databases with different edit histories
produce different dump bytes. This module sorts tables/indices by name explicitly and adds an
explicit ORDER BY per table's own primary-key columns, rather than relying on iterdump()'s
unordered per-table fetch."""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_ARCHIVE_KEEP = 24


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    pk_rows = [r for r in rows if r["pk"] > 0]
    pk_rows.sort(key=lambda r: r["pk"])  # PRAGMA table_info's pk column is the PK's column *order*
    return [r["name"] for r in pk_rows]


def serialize(conn: sqlite3.Connection) -> str:
    lines = ["BEGIN TRANSACTION;"]
    schema_rows = conn.execute(
        "SELECT name, type, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND type IN ('table', 'index') AND name != 'sqlite_sequence' "
        "ORDER BY name"
    ).fetchall()
    for row in schema_rows:
        if row["type"] != "table":
            continue
        lines.append(row["sql"] + ";")
        pk_cols = _primary_key_columns(conn, row["name"])
        order_by = f" ORDER BY {', '.join(pk_cols)}" if pk_cols else ""
        data_rows = conn.execute(f'SELECT * FROM "{row["name"]}"{order_by}').fetchall()
        for data_row in data_rows:
            values = ", ".join(_sql_literal(v) for v in tuple(data_row))
            lines.append(f'INSERT INTO "{row["name"]}" VALUES({values});')
    for row in schema_rows:
        if row["type"] == "index":
            lines.append(row["sql"] + ";")
    lines.append("COMMIT;")
    return "\n".join(lines)


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


@dataclass(frozen=True, slots=True)
class DumpInfo:
    checksum_valid: bool
    machine_id: str | None
    vector: dict[str, int]


def _dump_dir(project_root: Path) -> Path:
    return project_root / ".pdata-db-dump"


def write_latest(
    conn: sqlite3.Connection, *, project_root: Path, machine_id: str, vector: dict[str, int],
) -> None:
    text = serialize(conn)
    header = f"-- machine_id={machine_id}\n-- dumped_at={int(time.time())}\n"
    header += "\n".join(f"-- vector:{k}={v}" for k, v in sorted(vector.items())) + "\n"
    full_text = header + text
    checksum = hashlib.sha256(full_text.encode()).hexdigest()

    dump_dir = _dump_dir(project_root)
    archive_dir = dump_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    latest = dump_dir / "latest.sql"
    if latest.exists():
        shutil.copy2(latest, archive_dir / f"{int(time.time())}.sql")
        _prune_archive(archive_dir)

    latest.write_text(full_text)
    (dump_dir / "latest.sha256").write_text(checksum)


def _prune_archive(archive_dir: Path) -> None:
    files = sorted(archive_dir.glob("*.sql"))
    for stale in files[:-_ARCHIVE_KEEP] if len(files) > _ARCHIVE_KEEP else []:
        stale.unlink()


def read_latest(project_root: Path) -> DumpInfo:
    dump_dir = _dump_dir(project_root)
    latest = dump_dir / "latest.sql"
    checksum_file = dump_dir / "latest.sha256"
    if not latest.exists() or not checksum_file.exists():
        return DumpInfo(checksum_valid=False, machine_id=None, vector={})
    text = latest.read_text()
    actual = hashlib.sha256(text.encode()).hexdigest()
    expected = checksum_file.read_text().strip()
    if actual != expected:
        return DumpInfo(checksum_valid=False, machine_id=None, vector={})
    machine_id = None
    vector: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("-- machine_id="):
            machine_id = line.removeprefix("-- machine_id=")
        elif line.startswith("-- vector:"):
            rest = line.removeprefix("-- vector:")
            k, _, v = rest.partition("=")
            vector[k] = int(v)
    return DumpInfo(checksum_valid=True, machine_id=machine_id, vector=vector)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/pdata/test_dump.py -v`
Expected: 5 passed. If the determinism test (`test_dump_is_identical_regardless_of...`) fails,
that is the signal to fix - do not weaken the test.

- [ ] **Step 5: `mypy --strict`, then commit**

```bash
mypy --strict src/cc_session_tools/lib/pdata/dump.py
git add src/cc_session_tools/lib/pdata/dump.py tests/pdata/test_dump.py
git commit -m "pdata sync: deterministic dump/checksum/archive, not raw iterdump()"
```

---

## Task 6: Per-DB non-blocking lock check + atomic rehydrate swap

**Files:**
- Create: `src/cc_session_tools/lib/pdata/sync_lock.py`
- Create: `src/cc_session_tools/lib/pdata/rehydrate.py`
- Test: `tests/pdata/test_sync_lock.py`, `tests/pdata/test_rehydrate.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/pdata/test_sync_lock.py
def test_is_locked_false_when_nothing_else_holds_it(tmp_path):
    db_path = tmp_path / "p.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    assert sync_lock.is_locked(db_path) is False


def test_is_locked_true_while_another_connection_holds_begin_immediate(tmp_path):
    db_path = tmp_path / "p.db"
    holder = sqlite3.connect(db_path)
    holder.execute("CREATE TABLE t(x)")
    holder.execute("BEGIN IMMEDIATE")
    assert sync_lock.is_locked(db_path) is True
    holder.execute("ROLLBACK")
```

```python
# tests/pdata/test_rehydrate.py - uses vector_clock.Comparison + dump.write_latest from prior
# tasks to build realistic fixtures; covers: DUMP_DOMINATES -> swap happens and vector updates;
# LOCAL_DOMINATES -> no-op, file untouched; FORK -> no-op, result says FORK, nothing written;
# checksum failure -> result says CHECKSUM_INVALID, nothing written; locked DB -> result says
# DEFERRED (not attempted), nothing written.
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/pdata/test_sync_lock.py tests/pdata/test_rehydrate.py -v`
Expected: FAIL - `ModuleNotFoundError`.

- [ ] **Step 3: Implement `sync_lock.py`**

```python
"""Non-blocking "is someone else writing to this .db right now" check (spec: "Process safety").
Deliberately narrower than the superseded 2026-08-02 spec's whole-machine process check - this
only ever answers for one specific file, compatible with automatic (hook/cron) triggers."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def is_locked(db_path: Path) -> bool:
    conn = sqlite3.connect(db_path, timeout=0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
        return False
    except sqlite3.OperationalError:
        return True
    finally:
        conn.close()
```

- [ ] **Step 4: Implement `rehydrate.py`**

```python
"""Applies the spec's rehydrate comparison/swap for one project. Used by SessionStart, the hourly
cron job, and `ccst pdata rehydrate`."""
from __future__ import annotations

import enum
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.pdata import dump, repository, store, vector_clock, vector_clock_store


class RehydrateOutcome(enum.Enum):
    FAST_FORWARDED = "fast_forwarded"
    NO_OP = "no_op"            # local already at or ahead of the dump
    FORK = "fork"               # surfaced, nothing written
    CHECKSUM_INVALID = "checksum_invalid"  # surfaced, nothing written
    DEFERRED = "deferred"       # another writer holds the lock right now - retry later


@dataclass(frozen=True, slots=True)
class RehydrateResult:
    outcome: RehydrateOutcome
    from_machine: str | None = None


def rehydrate(project: str, *, force: bool = False) -> RehydrateResult:
    project_root = store.project_root(project)  # see Task 8 - init_paths' helper, reused here
    info = dump.read_latest(project_root)
    if not info.checksum_valid:
        return RehydrateResult(outcome=RehydrateOutcome.CHECKSUM_INVALID)

    conn = repository.connect(project)
    local_vector = vector_clock_store.read_vector(conn)
    comparison = vector_clock.compare(local=local_vector, dump=info.vector)

    if comparison == vector_clock.Comparison.LOCAL_DOMINATES and not force:
        return RehydrateResult(outcome=RehydrateOutcome.NO_OP)
    if comparison == vector_clock.Comparison.FORK and not force:
        return RehydrateResult(outcome=RehydrateOutcome.FORK, from_machine=info.machine_id)

    db_path = store.db_path(project)
    if sync_lock_is_locked(db_path):
        return RehydrateResult(outcome=RehydrateOutcome.DEFERRED)

    with tempfile.NamedTemporaryFile(dir=db_path.parent, suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    tmp_conn = sqlite3.connect(tmp_path)
    latest = project_root / ".pdata-db-dump" / "latest.sql"
    tmp_conn.executescript(_strip_comment_lines(latest.read_text()))
    tmp_conn.commit()
    tmp_conn.close()
    conn.close()
    tmp_path.replace(db_path)  # atomic on the same filesystem

    return RehydrateResult(outcome=RehydrateOutcome.FAST_FORWARDED, from_machine=info.machine_id)


def _strip_comment_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("--"))


def sync_lock_is_locked(db_path: Path) -> bool:
    from cc_session_tools.lib.pdata import sync_lock

    return sync_lock.is_locked(db_path)
```

Note: `store.project_root()` does not exist yet at this point in the plan - it's added in Task 8
(adopt-from-dump) since that's the first place a project *root* (as opposed to a project's `.db`
path) is needed inside `lib/pdata/`. If implementing tasks out of order, do Task 8's `store.py`
addition first, or stub `project_root()` temporarily and replace it when Task 8 lands.

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/pdata/test_sync_lock.py tests/pdata/test_rehydrate.py -v`
Expected: all pass.

- [ ] **Step 6: `mypy --strict`, then commit**

```bash
mypy --strict src/cc_session_tools/lib/pdata/sync_lock.py src/cc_session_tools/lib/pdata/rehydrate.py
git add src/cc_session_tools/lib/pdata/sync_lock.py src/cc_session_tools/lib/pdata/rehydrate.py tests/pdata/test_sync_lock.py tests/pdata/test_rehydrate.py
git commit -m "pdata sync: non-blocking lock check + atomic rehydrate swap"
```

---

## Task 7: Project-occupancy check

**Files:**
- Create: `src/cc_session_tools/lib/occupancy.py`
- Test: `tests/test_occupancy.py`

**Test convention, binding (matches the superseded spec's own rule for the same kind of check):**
every test monkeypatches `subprocess.run`/the specific `pgrep`/`readlink` wrapper functions -
never spawns or kills a real process.

- [ ] **Step 1: Write the failing tests**

```python
def test_occupied_true_when_a_claude_pid_matches_the_project_root(monkeypatch, tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [111, 222])
    monkeypatch.setattr(occupancy, "_cwd_of_pid", lambda pid: project_root if pid == 222 else tmp_path / "other")
    assert occupancy.is_occupied(project_root) is True


def test_occupied_false_when_no_pid_matches(monkeypatch, tmp_path):
    project_root = tmp_path / "proj"
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [111])
    monkeypatch.setattr(occupancy, "_cwd_of_pid", lambda pid: tmp_path / "other")
    assert occupancy.is_occupied(project_root) is False


def test_excludes_the_given_pid(monkeypatch, tmp_path):
    project_root = tmp_path / "proj"
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [222])
    monkeypatch.setattr(occupancy, "_cwd_of_pid", lambda pid: project_root)
    assert occupancy.is_occupied(project_root, exclude_pid=222) is False


def test_fails_safe_occupied_when_cwd_cannot_be_resolved(monkeypatch, tmp_path):
    project_root = tmp_path / "proj"
    monkeypatch.setattr(occupancy, "_claude_pids", lambda: [111])
    def raise_err(pid: int) -> None:
        raise OSError("no such process")
    monkeypatch.setattr(occupancy, "_cwd_of_pid", raise_err)
    assert occupancy.is_occupied(project_root) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_occupancy.py -v`
Expected: FAIL - `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Is a live `claude` process currently working in a given project (spec: "Process safety").
Fails safe: any error resolving a PID's cwd counts as occupied, never as clear."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _claude_pids() -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-x", "claude"], capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(p) for p in out.split() if p.strip().isdigit()]


def _cwd_of_pid(pid: int) -> Path:
    if sys.platform == "darwin":
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        for line in out.splitlines():
            if line.startswith("n"):
                return Path(line[1:])
        raise OSError(f"lsof returned no cwd line for pid {pid}")
    return Path(os.readlink(f"/proc/{pid}/cwd"))


def is_occupied(project_root: Path, *, exclude_pid: int | None = None) -> bool:
    resolved_root = project_root.resolve()
    for pid in _claude_pids():
        if pid == exclude_pid:
            continue
        try:
            cwd = _cwd_of_pid(pid).resolve()
        except OSError:
            return True  # fail safe, never fail open
        if cwd == resolved_root:
            return True
    return False
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_occupancy.py -v`
Expected: 4 passed.

- [ ] **Step 5: One manual, real-process confirmation** (not part of the automated suite - a
  one-off sanity check, matching what was done live during design): from a running Claude Code
  session, run `pgrep -x claude` and `readlink -f /proc/<that-pid>/cwd`, confirm the path matches
  the project you're actually in. This was already done once during brainstorming on WSL2 and
  passed; re-confirm after this module exists, and separately confirm the macOS `lsof` branch on
  an actual Mac before considering this task done cross-platform.

- [ ] **Step 6: `mypy --strict`, then commit**

```bash
mypy --strict src/cc_session_tools/lib/occupancy.py
git add src/cc_session_tools/lib/occupancy.py tests/test_occupancy.py
git commit -m "pdata sync: project-occupancy check (pgrep + /proc/cwd or macOS lsof)"
```

---

## Task 8: Adopt-from-dump in `ccst pdata init`, and a shared `project_root()` helper

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/store.py` (add `project_root(project) -> Path`,
  thin wrapper over `init_paths.default_projects_root() / project` - reused by `rehydrate.py`,
  Task 6)
- Modify: `src/cc_session_tools/lib/pdata/init_service.py`
- Test: `tests/pdata/test_init_service.py` (extend existing)

- [ ] **Step 1: Write the failing test**

```python
def test_init_adopts_from_an_existing_valid_dump_instead_of_classifying(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    project_root = tmp_path / "proj"
    project_root.mkdir()
    # Build a dump as if another machine had already published one.
    con = repository.connect("proj")  # writes to a DB this test then discards
    with repository._immediate(con):
        vector_clock_store.write_vector(con, {"macbook": 3}, updated_at=100)
    dump.write_latest(con, project_root=project_root, machine_id="macbook", vector={"macbook": 3})
    (tmp_path / "dbs" / "proj.db").unlink()  # simulate: no local .db yet on *this* machine

    result = init_service.write(project="proj", rehearse=None)

    assert result.adopted_from_dump is True
    conn = repository.connect("proj")
    assert vector_clock_store.read_vector(conn) == {"macbook": 3}
```

(Exact `init_service.write(...)`'s real signature and return type must be read from the existing
module before writing this test - the call above is illustrative of intent, not a literal
contract; match what's actually there, extending its result type with an `adopted_from_dump: bool`
field rather than inventing a parallel return path.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/pdata/test_init_service.py -k adopt -v`
Expected: FAIL - classification runs instead of adoption (or `AttributeError` on the new field).

- [ ] **Step 3: Implement** - at the top of `init_service.write()` (before any classification
  logic runs), check `dump.read_latest(project_root)`; if `checksum_valid` and no local `.db`
  exists yet (`store.db_path(project).exists()` is `False`), call `rehydrate.rehydrate(project,
  force=True)` (force, because there's no local vector to compare against - trivially a clean
  adopt) and return early with `adopted_from_dump=True`, printing the "Adopting existing pdata
  from sync dump (published by X, at Y) - skipping file classification/import" message the spec
  names. If the dump exists but fails its checksum, fail with the same corrupt-dump guidance used
  elsewhere (point at `ccst pdata resolve`'s messaging, Task 9) rather than silently falling
  through to classification.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/pdata/test_init_service.py -v`
Expected: all pass, including the new one.

- [ ] **Step 5: `mypy --strict`, then commit**

```bash
mypy --strict src/cc_session_tools/lib/pdata/init_service.py src/cc_session_tools/lib/pdata/store.py
git add src/cc_session_tools/lib/pdata/store.py src/cc_session_tools/lib/pdata/init_service.py tests/pdata/test_init_service.py
git commit -m "pdata sync: ccst pdata init adopts from an existing dump instead of classifying"
```

---

## Task 9: Conflict resolution - relational-integrity-safe, per-record

**Files:**
- Create: `src/cc_session_tools/lib/pdata/resolve.py`
- Test: `tests/pdata/test_resolve.py`
- Modify: `src/cc_session_tools/skills/pm-pdata-conflict-resolution/SKILL.md` (new section)

**Read first:** the spec's "Conflict handling & notification" relational-integrity paragraph and
its "Post-resolve vector-clock update" paragraph - this task implements both, together, since
they're one atomic operation, not two.

- [ ] **Step 1: Write the failing tests**

```python
def test_diff_pairs_base_and_extension_rows_together(tmp_path, monkeypatch):
    # Build a local db and a dump that differ in one record's base content AND its extension
    # field - diff_against_dump() must report them as one combined difference, not two.
    ...


def test_diff_flags_a_schema_catalog_field_present_only_in_the_dump(tmp_path, monkeypatch):
    ...


def test_diff_reports_delete_vs_update_as_its_own_category(tmp_path, monkeypatch):
    ...


def test_apply_resolution_adds_the_missing_extension_column_before_inserting_the_row(tmp_path, monkeypatch):
    ...


def test_apply_resolution_bumps_own_revision_once_regardless_of_record_count(tmp_path, monkeypatch):
    ...


def test_apply_resolution_adopts_the_dump_machines_revision_and_maxes_others(tmp_path, monkeypatch):
    ...


def test_apply_resolution_immediately_writes_a_fresh_dump(tmp_path, monkeypatch):
    ...
```

(Fill in each body once the diff/apply function signatures exist - write these as real assertions
against real return values, not placeholders, before moving to Step 3; the seven names above are
the minimum coverage this task needs, one per requirement in the spec paragraphs it implements.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/pdata/test_resolve.py -v`
Expected: FAIL - `ModuleNotFoundError`.

- [ ] **Step 3: Implement `resolve.py`** covering:
  - `diff_against_dump(project) -> ResolveDiff` - three categories: base+extension record pairs
    that differ (paired, never split), schema-catalog (`record_group_fields`) rows present on
    only one side, and delete-vs-update pairs flagged explicitly as that category.
  - `apply_resolution(project, choices)` - for each record, reconcile schema first (add any
    missing `ext_<group>` column via the existing `ALTER TABLE ADD COLUMN` path before inserting
    a row that needs it), then write base+extension together in one transaction; once every
    chosen record is applied, in that **same** transaction: `vector_clock.bump_own(local, self)`,
    `vector_clock.merge(local, dump_vector)` adopting the dump's value for every other machine,
    `vector_clock_store.write_vector(...)`; then, after committing, call `dump.write_latest(...)`
    immediately (outside the DB transaction, since it's a filesystem write) - matching rehydrate's
    "always immediately re-dump" rule.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/pdata/test_resolve.py -v`
Expected: all pass.

- [ ] **Step 5: Extend the skill doc** - add a new `## Cross-machine fork` section to
  `pm-pdata-conflict-resolution/SKILL.md`, alongside (not replacing) its existing single-file
  section, describing when `ccst pdata resolve` triggers this vs. the existing exit-3 case, and
  reusing that section's existing "never auto-merge, always surface to Chris" framing verbatim
  rather than restating it differently.

- [ ] **Step 6: `mypy --strict`, then commit**

```bash
mypy --strict src/cc_session_tools/lib/pdata/resolve.py
git add src/cc_session_tools/lib/pdata/resolve.py tests/pdata/test_resolve.py src/cc_session_tools/skills/pm-pdata-conflict-resolution/SKILL.md
git commit -m "pdata sync: relational-integrity-safe conflict resolution + post-resolve vector bookkeeping"
```

---

## Task 10: `ccst pdata dump/rehydrate/resolve` CLI + notification

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py` (three new subcommands, pattern-matched on the
  existing `pdata_sub.add_parser(...)` style at `ccst.py:2321` onward)
- Test: `tests/test_ccst_pdata_sync_cli.py` (new, subprocess-based, matching
  `tests/test_ccst_pdata_reconcile_cli.py`'s `_run()`/`base_env` pattern)

- [ ] **Step 1: Write the failing tests** covering, for each of `dump`/`rehydrate`/`resolve`:
  the happy path, `--project` vs `--all`, `--force`, and (for `dump`/`rehydrate`) the
  conflict/checksum-failure case printing a message that names `ccst pdata resolve`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement the three subcommands**, each a thin wrapper calling
  `dump.write_latest`/`rehydrate.rehydrate`/`resolve.apply_resolution` and formatting the result.
  On any `CHECKSUM_INVALID`/`FORK` outcome (from `dump` or `rehydrate`), call the notification
  helper from Task 11 before returning a non-zero exit code - never a bare `print` to stderr only.

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: `mypy --strict`, full suite, commit.**

```bash
mypy --strict src/cc_session_tools/cli/ccst.py
pytest -q
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_pdata_sync_cli.py
git commit -m "pdata sync: ccst pdata dump/rehydrate/resolve CLI"
```

---

## Task 11: Notification - Telegram + SessionStart digest

**Files:**
- Create: `src/cc_session_tools/lib/pdata/sync_notify.py`
- Test: `tests/pdata/test_sync_notify.py`

**Read first:** how the existing `notify-user` skill sends a Telegram message, and how the
existing SessionStart digest mechanism (the one already printing cc-scheduler catch-up and
pending-rename notices each session) is fed - find its source module via `grep -rn
"cc-scheduler\|pending-rename" src/` before writing this task's implementation, and reuse that
exact mechanism rather than inventing a second one. This task's scope is wiring a pdata-conflict
message into both already-existing channels, not building either channel.

- [ ] **Step 1: Write the failing test(s)**, monkeypatching whatever the Telegram-send function
  and the digest-queue function turn out to be (named precisely once found above).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** `notify_conflict(project: str, *, outcome: str, detail: str) -> None`
  calling both existing mechanisms once each.

- [ ] **Step 4: Run to verify pass, `mypy --strict`, commit.**

```bash
git add src/cc_session_tools/lib/pdata/sync_notify.py tests/pdata/test_sync_notify.py
git commit -m "pdata sync: wire conflict notifications into existing Telegram + digest channels"
```

---

## Task 12: SessionStart / SessionEnd hooks

**Files:**
- Create: `src/cccs_hooks/pdata_sync.py`
- Test: `tests/hooks/test_pdata_sync_hook.py` (check `tests/hooks/` or equivalent for the existing
  per-hook test layout and match it - do not invent a new layout)

- [ ] **Step 1: Write the failing tests** - `on_session_start(cwd)`: occupancy-check (excluding
  own PID via `os.getppid()`) → if occupied, no-op; else `rehydrate.rehydrate(project)`, and on
  `FAST_FORWARDED` print the exact digest line the spec names ("Re-hydrating project pdata DB
  based on updates made on `<machine>` at `<timestamp>`"), on `FORK`/`CHECKSUM_INVALID` call
  `sync_notify.notify_conflict(...)`. `on_session_end(cwd)`: no occupancy check (per spec); the
  dump-worthy condition from "Triggers", calling `dump.write_latest` or `sync_notify`.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement**, confirming the actual Claude Code hook event name for "session end"
  against the installed hook schema first (the spec flags this as unconfirmed - called "SessionEnd"
  throughout, verify the real name before wiring the entry point).

- [ ] **Step 4: Run to verify pass, `mypy --strict`, commit.**

```bash
git add src/cccs_hooks/pdata_sync.py tests/hooks/test_pdata_sync_hook.py
git commit -m "pdata sync: SessionStart/SessionEnd hook wiring"
```

---

## Task 13: Bundled hourly `ccsched` job

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/bundled_jobs.py`
- Test: `tests/test_scheduler_bundled_jobs.py` (extend existing - read its existing
  `pdata-verify-all`/`pm-session-output-reconcile` entries first and match their shape exactly)

- [ ] **Step 1: Write the failing test** - a new bundled job entry exists, hourly cadence, command
  resolving to whatever CLI entry point Task 10 exposes for "run the full check for every
  in-scope project" (likely a new `--all-projects` mode on top of `dump`/`rehydrate` that does
  rehydrate-check-then-dump-check per project, per "Triggers" - confirm against Task 10's actual
  CLI surface before writing this job's `command` tuple).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** the bundled job entry.

- [ ] **Step 4: Run to verify pass, commit.**

```bash
git add src/cc_session_tools/lib/scheduler/bundled_jobs.py tests/test_scheduler_bundled_jobs.py
git commit -m "pdata sync: register the hourly sync job as a bundled ccsched job"
```

---

## Task 14: Full suite, changelog, version bump

- [ ] **Step 1:** `pytest -q` - full suite green.
- [ ] **Step 2:** `mypy --strict` across every new/modified module (not just the per-task spot
  checks already run).
- [ ] **Step 3:** Manually verify against `home` following the spec's "Manual end-to-end
  verification" section, all 9 numbered steps, on both real laptops - not automatable, do not
  skip.
- [ ] **Step 4:** `pyproject.toml` version bump and a `CHANGELOG.md` `## [2.12.0]` entry
  summarising the feature (this is the point this plan's earlier note about "changelog once real
  implementation lands" refers to).
- [ ] **Step 5:** Commit, push, open the PR merging this feature branch back to `main` (per
  Chris's established workflow this session - confirm the target branch name before opening it).

---

## Plan Review Loop

Dispatch a single plan-document-reviewer subagent against this file + the spec document before
starting execution. Fix and re-dispatch on Issues Found, up to 3 iterations, then surface to Chris.
