# Phase 1: Shared data-store infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Read `2026-07-13-data-store-uplift-00-overview.md` first — it fixes the env-var conventions and
> connection-helper contract this plan implements. Every later phase (2-6) imports from the two
> modules built here.

**Goal:** build `lib/paths.py` (root-B path resolution) and `lib/db.py` (shared SQLite
connection-setup + backup-checkpoint helpers), so every subsequent phase opens its `.db` file
through one consistently-configured code path instead of repeating pragma setup ad hoc.

**Architecture:** two small, dependency-free modules under `src/cc_session_tools/lib/`. No
existing store is touched in this phase — it ships inert (imported by nothing) until Phase 2
starts consuming it. This keeps the phase safely mergeable on its own.

**Tech Stack:** Python 3.11 stdlib (`sqlite3`, `pathlib`, `os`), pytest, `monkeypatch`.

---

## First task: sync branch with main

- [ ] **Step 1: Merge `main` into `f/claude-data-store-uplift`**

`main` is 1 commit ahead of this branch (PR #71 merge-back of unrelated markers/catchup/gc work
that originated on this same branch name previously). Sync before starting:

```bash
git fetch origin
git merge origin/main
```

Expected: fast-forward or trivial merge, no conflicts (the branch's only local change is an
uncommitted `.claude/CLAUDE.md` edit, which does not conflict with PR #71's file set).

- [ ] **Step 2: Confirm clean state**

```bash
git status
git log --oneline -3
```

Expected: branch now includes `76b7ad8` in its history; working tree still shows the uncommitted
`.claude/CLAUDE.md` modification and the untracked `docs/superpowers/specs/2026-07-08-ccsched-one-shot-future-dated-jobs.md` (both pre-existing, unrelated to this plan — leave them).

---

## File Structure

- Create: `src/cc_session_tools/lib/paths.py`
- Create: `src/cc_session_tools/lib/db.py`
- Test: `tests/test_paths.py`
- Test: `tests/test_db.py`

No existing file is modified in this phase.

---

### Task 1: `lib/paths.py` — data-home root resolution

**Files:**
- Create: `src/cc_session_tools/lib/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paths.py
from pathlib import Path

from cc_session_tools.lib import paths


def test_data_home_defaults_to_xdg_data_claude(monkeypatch, tmp_path):
    monkeypatch.delenv("CCST_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.data_home() == tmp_path / ".local" / "share" / "claude"


def test_data_home_honours_env_override(monkeypatch, tmp_path):
    override = tmp_path / "custom-data-home"
    monkeypatch.setenv("CCST_DATA_HOME", str(override))
    assert paths.data_home() == override


def test_data_home_env_override_beats_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "unused-home"))
    override = tmp_path / "custom-data-home"
    monkeypatch.setenv("CCST_DATA_HOME", str(override))
    assert paths.data_home() == override
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.paths'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/paths.py
"""Root-B path resolution: ~/.local/share/claude and its per-store filenames.

Root B holds everything this repo's tooling creates that (a) isn't Claude
Code's own native store under ~/.claude, and (b) isn't safe to assume
machine-portable. See data-stores-design-spec.md Sections 1-2 (session
20260712-claude-finalise-common-extra-claude-data-store-requirements) for
the full placement rationale. Never assumed to sync across machines.
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_HOME_ENV = "CCST_DATA_HOME"


def data_home() -> Path:
    """Root B directory. Overridable via CCST_DATA_HOME (tests / non-standard setups)."""
    override = os.environ.get(DATA_HOME_ENV)
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "claude"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_paths.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/paths.py tests/test_paths.py
git commit -m "feat(lib): add paths.data_home() — root-B path resolution"
```

---

### Task 2: `lib/db.py` — connection-setup helper

**Files:**
- Create: `src/cc_session_tools/lib/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_db.py
import sqlite3
import threading

import pytest

from cc_session_tools.lib import db

_DDL = """
CREATE TABLE IF NOT EXISTS widgets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
"""


def test_connect_creates_parent_dir_and_applies_pragmas(tmp_path):
    target = tmp_path / "nested" / "store.db"
    conn = db.connect(target, ddl=_DDL)
    try:
        assert target.exists()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
    finally:
        conn.close()


def test_connect_runs_ddl_idempotently(tmp_path):
    target = tmp_path / "store.db"
    conn1 = db.connect(target, ddl=_DDL)
    conn1.execute("INSERT INTO widgets (name) VALUES ('a')")
    conn1.commit()
    conn1.close()

    # Re-running DDL on the same file must not error or wipe data.
    conn2 = db.connect(target, ddl=_DDL)
    rows = conn2.execute("SELECT name FROM widgets").fetchall()
    conn2.close()
    assert [r["name"] for r in rows] == ["a"]


def test_connect_row_factory_supports_dict_style_access(tmp_path):
    conn = db.connect(tmp_path / "store.db", ddl=_DDL)
    conn.execute("INSERT INTO widgets (name) VALUES ('x')")
    conn.commit()
    row = conn.execute("SELECT * FROM widgets").fetchone()
    conn.close()
    assert row["name"] == "x"


def test_connect_readonly_cannot_write(tmp_path):
    target = tmp_path / "store.db"
    setup = db.connect(target, ddl=_DDL)
    setup.close()

    conn = db.connect(target, readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO widgets (name) VALUES ('nope')")
    finally:
        conn.close()


def test_connect_readonly_missing_file_raises(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        db.connect(tmp_path / "missing.db", readonly=True)


def test_connect_rejects_old_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 30, 0))
    with pytest.raises(RuntimeError, match="too old"):
        db.connect(tmp_path / "store.db", ddl=_DDL)


def test_concurrent_writers_do_not_corrupt(tmp_path):
    target = tmp_path / "store.db"
    db.connect(target, ddl=_DDL).close()

    errors = []

    def writer(i):
        try:
            conn = db.connect(target)
            conn.execute("INSERT INTO widgets (name) VALUES (?)", (f"w{i}",))
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    conn = db.connect(target)
    count = conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0]
    conn.close()
    assert count == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.db'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/db.py
"""Shared SQLite connection-setup helper for every cc_session_tools .db store.

Every .db file under paths.data_home() opens through connect() so WAL mode
and an explicit busy-timeout are applied consistently, rather than each
subsystem module repeating pragma setup ad hoc. This is what prevents a
repeat of the exact drift that left statusline-usage.db (a different repo)
without WAL mode while its sibling command-cache.db got it right — see
data-stores-design-spec.md Section 7.3.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_BUSY_TIMEOUT_MS = 5000
_MIN_SQLITE_VERSION = (3, 35, 0)


def connect(path: Path, *, ddl: str | None = None, readonly: bool = False) -> sqlite3.Connection:
    """Open path with WAL mode, an explicit busy-timeout, and dict-style rows.

    ddl, if given, is a CREATE TABLE/INDEX/VIEW IF NOT EXISTS multi-statement
    string executed (and committed) once per call — safe to pass on every
    connect(), including against an already-initialised file.

    readonly opens the file via a file: URI in mode=ro; ddl is ignored (and
    must be None) in that mode since a read-only handle cannot create schema.
    """
    if sqlite3.sqlite_version_info < _MIN_SQLITE_VERSION:
        raise RuntimeError(
            f"sqlite3 {sqlite3.sqlite_version} is too old (need >= "
            f"{'.'.join(map(str, _MIN_SQLITE_VERSION))}) for "
            "CREATE ... IF NOT EXISTS support"
        )

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    if readonly:
        if ddl is not None:
            raise ValueError("ddl is not supported with readonly=True")
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=_BUSY_TIMEOUT_MS / 1000, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_MS / 1000, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys=ON")
        if ddl:
            conn.executescript(ddl)
            conn.commit()

    conn.row_factory = sqlite3.Row
    return conn


def checkpoint(conn: sqlite3.Connection) -> None:
    """Force a WAL checkpoint. Call before any filesystem-level copy of a live .db file."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def backup_to(source_path: Path, dest_path: Path) -> None:
    """Safely copy a live WAL-mode .db file using SQLite's own backup API.

    Safe against concurrent writers on source_path — no manual checkpoint or
    cp needed (sqlite3.Connection.backup() handles this internally). Used by
    `ccst backup run` (Phase 7) and by migration scripts' pre-cutover safety
    copies.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source_path))
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/db.py tests/test_db.py
git commit -m "feat(lib): add db.connect() — shared WAL + busy-timeout connection helper"
```

---

### Task 3: `lib/db.py` — `backup_to()` round-trip test

**Files:**
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_db.py

def test_backup_to_copies_committed_data(tmp_path):
    source = tmp_path / "source.db"
    dest = tmp_path / "backups" / "source-copy.db"

    conn = db.connect(source, ddl=_DDL)
    conn.execute("INSERT INTO widgets (name) VALUES ('backed-up')")
    conn.commit()
    conn.close()

    db.backup_to(source, dest)

    assert dest.exists()
    check = sqlite3.connect(str(dest))
    rows = check.execute("SELECT name FROM widgets").fetchall()
    check.close()
    assert rows == [("backed-up",)]


def test_checkpoint_does_not_error_on_fresh_connection(tmp_path):
    conn = db.connect(tmp_path / "store.db", ddl=_DDL)
    db.checkpoint(conn)  # must not raise
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_backup_to_copies_committed_data -v`
Expected: FAIL — `dest` never created (function doesn't exist yet is already implemented in Task
2's `db.py`; if Task 2 was done in order this instead should already PASS — run it to confirm,
this step exists to catch ordering mistakes)

- [ ] **Step 3: Run full test file to verify all pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (9 tests total)

- [ ] **Step 4: Commit**

```bash
git add tests/test_db.py
git commit -m "test(lib): cover db.backup_to() and db.checkpoint()"
```

---

## Verification

- [ ] **Run the full test suite to confirm no regressions**

```bash
uv run pytest -q
```

Expected: all tests pass, including the 9 new tests in `test_db.py` and 3 in `test_paths.py`; no
existing test touches `lib/db.py` or `lib/paths.py` yet so nothing else should change.

- [ ] **Run the linter/type-checker if configured**

```bash
uv run ruff check src/cc_session_tools/lib/db.py src/cc_session_tools/lib/paths.py
uv run mypy src/cc_session_tools/lib/db.py src/cc_session_tools/lib/paths.py
```

(Check `pyproject.toml` / `Makefile` / CI config first for the exact configured commands if these
differ — match whatever this repo's own check suite actually runs.)

## Handoff

Phase 1 is complete when `lib/db.py` and `lib/paths.py` are merged with passing tests and no
other module imports them yet. Phases 2-6 can now start in parallel, each importing
`cc_session_tools.lib.db` and `cc_session_tools.lib.paths` per the contract fixed in
`2026-07-13-data-store-uplift-00-overview.md`.
