# Phase 6: command-cache.db path move + claude-flags relocate-and-fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Read `2026-07-13-data-store-uplift-00-overview.md` and `2026-07-13-data-store-uplift-01-shared-infra.md`
> first — this plan consumes `cc_session_tools.lib.db.connect()` and `cc_session_tools.lib.paths.data_home()`
> built in Phase 1. **Phase 1 must be merged before this phase starts** (its modules don't exist in
> the tree yet as of this writing).

**Goal:** relocate `command-cache.db` and the `claude-flags.json` cache under the new
`~/.local/share/claude` root (`data_home()`), and fix `claude-flags.json`'s non-atomic write —
without changing either store's external behaviour (cache semantics, `cccs-stats` output, `ccr`'s
flag-validation behaviour).

**Architecture:** two small, independent, mechanically simple sub-tasks bundled into one phase
because both are low-risk and low-effort:
- **Part A** (`command-cache.db`): pure path-constant relocation, plus switching two hand-rolled
  SQLite connectors (`cccs_hooks/cache.py`, `cccs_hooks/stats.py`) onto Phase 1's shared
  `lib/db.connect()` helper, eliminating duplicate pragma-setup code.
- **Part B** (`claude-flags.json`): path relocation to the new env-var convention, plus a
  non-atomic-write bugfix using the codebase's existing `write_json_atomic()` helper. This store
  stays a flat JSON file — it is explicitly **not** converted to SQLite (see Decision 4 below).

Neither store needs a migration script: both are fully-regenerable caches (see Decisions 3 and 5).

**Tech Stack:** Python 3.11 stdlib (`sqlite3`, `json`, `pathlib`, `os`), pytest, `monkeypatch`,
`unittest.mock`.

---

## Decisions (binding for this phase — read before starting)

### Decision 1: Part A also switches `_connect()`/`_connect_readonly()` to the shared helper

The task could (a) touch only the one path constant, or (b) also switch both connectors in
`cccs_hooks/cache.py` and `cccs_hooks/stats.py` onto `cc_session_tools.lib.db.connect()`, deleting
the now-duplicate hand-rolled pragma setup. **Chosen: (b).** `cache.py`'s existing `_connect()` is
already functionally equivalent (WAL, an effective busy-timeout via the `timeout=5.0` connect
kwarg, a version guard, `executescript(_DDL)`) — but it is a second, independent implementation of
exactly what Phase 1's `lib/db.py` now exists to centralise. Leaving it as hand-rolled code while a
shared helper sits unused one import away is the same drift the design spec's §7.3 calls out
(`statusline-usage.db` missing WAL while `command-cache.db` had it — divergent connectors is how
that happened). The tradeoff: this phase's diff is larger than a one-line constant change, and it
touches `_connect()`'s internals (though not its return type or callers' usage of it) and
`stats.py`'s connector. Both changes are covered by the existing and new test suites below.

### Decision 2: default paths become lazily-resolved, not frozen module constants

Both `cccs_hooks/cache.py`'s current `_DEFAULT_DB` and `cc_session_tools/lib/claude_flags.py`'s
current `_CACHE_DIR`/`_CACHE_FILE` are **module-level constants computed once at import time**
(`Path.home() / ...`). `data_home()` reads `CCST_DATA_HOME` from the environment on every call, so
if either module keeps a frozen constant built from `data_home()` at import time, tests that
`monkeypatch.setenv("CCST_DATA_HOME", ...)` *after* the module is already imported would silently
observe the stale pre-monkeypatch value — a real bug, not a hypothetical one, since pytest imports
test modules (and transitively the modules under test) once per session. Both parts of this phase
therefore replace the frozen constants with small functions (`_db_path()` already existed in
`cache.py` in this lazy form; Part B gains equivalent `_cache_dir()`/`_cache_file()` functions) that
call `data_home()` fresh on each invocation.

### Decision 3: no migration script for `command-cache.db` (Part A)

Confirmed safe to regenerate at the new location, orphaning the old file:
- `cache.py`'s own module docstring calls it a "SQLite-backed command cache" (line 1) and states
  entries "auto-prune... older than 90 days" (line 18) — nothing in the docstring or code claims
  any retention guarantee beyond that rolling 90-day window.
- `cache_lookup()` degrades gracefully on a miss: the caller (`bash_security_review` hook) just
  re-validates via Claude, exactly what happens on any first-ever run today.
- `hook_invocations` (the `cccs-stats` analytics table) losing history on relocation is the same
  category of loss as the 90-day auto-prune already performs routinely — not a new risk introduced
  by this phase.

No note anywhere in `cache.py` or `stats.py` contradicts "safe to regenerate." No migration-script
task is included. The old file at `~/.cache/claude/logs/command-cache.db` may be left in place
(harmless orphan) or removed by hand:
```bash
rm -f ~/.cache/claude/logs/command-cache.db
```

### Decision 4: `claude-flags.json` stays a flat file, not SQLite (Part B)

Per the design spec's §7.2 "not every store needs to become a database" carve-out: this is a
single small (`mtime`/`path`/`flags` triple), fully-regenerable, no-query-need blob. Converting it
to SQLite would add a schema and a connection helper for zero benefit — `json.loads`/`write_text`
is already the right tool. This phase relocates and atomic-fixes the flat file; it does not touch
its format.

### Decision 5: no migration script for `claude-flags.json` (Part B)

Confirmed safe to regenerate: the cache is invalidated and rebuilt automatically whenever the
`claude` binary's mtime changes from what's recorded (`get_claude_flags()`'s existing mtime-compare
logic, unchanged by this phase), and is rebuilt unconditionally whenever the file is simply absent.
Relocating the default path is indistinguishable, from the code's point of view, from the binary
having been upgraded — the next call regenerates it from `claude --help` output. No migration
script task is included. The old file may be left in place or removed by hand:
```bash
rm -rf ~/.cache/cc-session-tools
```

### Decision 6: `claude-flags.json` switches to the env-var convention

The current test suite (`tests/test_claude_flags.py`) redirects the cache location via
`monkeypatch.setattr(cf, "_CACHE_FILE", ...)` / `monkeypatch.setattr(cf, "_CACHE_DIR", ...)` —
i.e. patching module attributes directly, not an env var. Every other store in this migration
(Phases 2-5, and Part A of this phase) redirects via one dedicated environment variable per
subsystem, per the overview's binding convention table. **Chosen: switch to the env-var
convention** (`CCST_CLAUDE_FLAGS_DIR`, per the overview's table) for consistency with every other
phase, updating `tests/test_claude_flags.py` accordingly. The tradeoff is a slightly larger test
diff (3 existing tests change their redirection mechanism) versus leaving `_CACHE_FILE` as a
directly-patchable attribute (smaller diff, but a second, inconsistent test-seam pattern in the
same codebase — rejected).

---

## File Structure

- Modify: `src/cccs_hooks/cache.py`
- Modify: `src/cccs_hooks/stats.py`
- Modify: `src/cc_session_tools/lib/claude_flags.py`
- Modify: `tests/test_cache_sqlite.py`
- Modify: `tests/test_claude_flags.py`

No new files. No migration scripts (Decisions 3 and 5).

---

### Task 1: `cache.py` — relocate default DB path to `data_home()`

**Files:**
- Modify: `src/cccs_hooks/cache.py:15` (docstring), `:32` (`_DEFAULT_DB` constant, deleted),
  `:92-94` (`_db_path()`)
- Test: `tests/test_cache_sqlite.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cache_sqlite.py`:

```python
def test_default_db_path_uses_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CCCS_CACHE_DB", raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    from cccs_hooks.cache import _db_path
    assert _db_path() == tmp_path / "command-cache.db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cache_sqlite.py::test_default_db_path_uses_data_home -v`
Expected: FAIL — either `ModuleNotFoundError: No module named 'cc_session_tools.lib.paths'` (if
Phase 1 isn't merged yet — stop and merge Phase 1 first) or an assertion failure comparing against
the old `~/.cache/claude/logs/command-cache.db` default.

- [ ] **Step 3: Update the docstring and replace `_DEFAULT_DB` with a lazy default**

In `src/cccs_hooks/cache.py`, change the docstring line:

```python
# before (line 15)
DB path: CCCS_CACHE_DB env var, else ~/.cache/claude/logs/command-cache.db
```
```python
# after
DB path: CCCS_CACHE_DB env var (absolute file path), else
cc_session_tools.lib.paths.data_home() / "command-cache.db".
```

Delete the module-level constant (line 32):

```python
# delete this line entirely
_DEFAULT_DB = Path.home() / ".cache" / "claude" / "logs" / "command-cache.db"
```

Add the import (alongside the existing stdlib imports):

```python
from cc_session_tools.lib.paths import data_home
```

Replace `_db_path()`:

```python
def _db_path() -> Path:
    env = os.environ.get("CCCS_CACHE_DB", "").strip()
    if env:
        return Path(env)
    return data_home() / "command-cache.db"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cache_sqlite.py::test_default_db_path_uses_data_home -v`
Expected: PASS

- [ ] **Step 5: Run the full cache test file to confirm no regressions**

Run: `uv run pytest tests/test_cache_sqlite.py -v`
Expected: all PASS (every other test in this file sets `CCCS_CACHE_DB` explicitly via the `db`
fixture, so they're unaffected by the default-path change).

- [ ] **Step 6: Commit**

```bash
git add src/cccs_hooks/cache.py tests/test_cache_sqlite.py
git commit -m "feat(cache): relocate command-cache.db default path to data_home()"
```

---

### Task 2: `cache.py` — switch `_connect()` to the shared `lib.db.connect()` helper

**Files:**
- Modify: `src/cccs_hooks/cache.py:97-110` (`_connect()`)

- [ ] **Step 1: No new test needed for this step**

Every existing test in `tests/test_cache_sqlite.py` exercises `_connect()` indirectly through
`cache_lookup`/`cache_record`/`invocations_record`; they already assert on WAL-mode-dependent
concurrent-write safety (`test_concurrent_writes_do_not_corrupt`) and schema creation
(`test_lookup_empty_returns_none` implicitly creates the schema on first connect). These are
sufficient regression coverage for swapping the connector's internals — this step only needs the
implementation change plus a full re-run of the existing suite (Step 3 below).

- [ ] **Step 2: Replace `_connect()`**

Add the import (alongside the `data_home` import added in Task 1):

```python
from cc_session_tools.lib.db import connect as _sqlite_connect
```

Replace the body of `_connect()`:

```python
# before
def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
    if sqlite3.sqlite_version_info < (3, 35, 0):
        raise RuntimeError(
            f"SQLite >= 3.35.0 required (got {sqlite3.sqlite_version}); "
            "'CREATE VIEW IF NOT EXISTS' is not supported on older versions."
        )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)
    conn.commit()
    return conn
```
```python
# after
def _connect() -> sqlite3.Connection:
    return _sqlite_connect(_db_path(), ddl=_DDL)
```

Note: `lib.db.connect()`'s own version guard (`_MIN_SQLITE_VERSION = (3, 35, 0)`, matching this
file's requirement exactly since `_DDL` uses `CREATE VIEW IF NOT EXISTS`) replaces the inline
check; its `RuntimeError` message text differs slightly ("too old" vs. the old custom string) but
no test in this repo asserts on that message (confirmed by grep before writing this plan), so this
is not a behaviour change worth preserving byte-for-byte. `lib.db.connect()` also sets
`conn.row_factory = sqlite3.Row`, which `_connect()` did not do before — this is safe because
`sqlite3.Row` supports both `row["col"]` and positional iteration/unpacking, and every consumer in
this file (`_row_to_entry`, `cache_age_days`) uses tuple-unpacking or `row[0]` positional access,
both of which work unchanged against `sqlite3.Row`.

- [ ] **Step 3: Run the full test suite to verify no regressions**

Run: `uv run pytest tests/test_cache_sqlite.py tests/test_bash_security_review.py -v`
Expected: all PASS. `test_bash_security_review.py` exercises the same `_connect()` path through
the `bash_security_review` hook end-to-end and is the most sensitive regression check for this
change.

- [ ] **Step 4: Commit**

```bash
git add src/cccs_hooks/cache.py
git commit -m "refactor(cache): use shared lib.db.connect() helper, drop duplicate pragma setup"
```

---

### Task 3: `stats.py` — switch `_connect_readonly()` to the shared helper

**Files:**
- Modify: `src/cccs_hooks/stats.py:1-28`
- Test: `tests/test_cache_sqlite.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cache_sqlite.py`:

```python
def test_stats_main_no_db_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """cccs-stats prints a friendly message (not a traceback) when the DB file doesn't exist."""
    monkeypatch.setenv("CCCS_CACHE_DB", str(tmp_path / "does-not-exist.db"))
    monkeypatch.delenv("CCCS_CACHE_PATH", raising=False)
    from cccs_hooks import stats as stats_mod
    stats_mod.main([])
    out = capsys.readouterr().out
    assert "No hook DB found" in out
```

This test currently passes against the old implementation too (it's a regression guard, not a
new-behaviour test) — that's expected and fine; the point is to lock in the "missing file returns
`None`, not an exception" contract before changing how `_connect_readonly()` detects a missing
file (Step 3 removes the explicit `path.exists()` pre-check in favour of letting
`lib.db.connect(readonly=True)` raise and catching it).

- [ ] **Step 2: Run test to verify it currently passes (baseline)**

Run: `uv run pytest tests/test_cache_sqlite.py::test_stats_main_no_db_found -v`
Expected: PASS (baseline, against the pre-change implementation)

- [ ] **Step 3: Replace `_connect_readonly()`**

```python
# before (full function + now-unused imports)
import argparse
import sqlite3
import urllib.parse
from pathlib import Path

from cccs_hooks.cache import _db_path


def _connect_readonly() -> sqlite3.Connection | None:
    path = _db_path()
    if not path.exists():
        return None
    try:
        encoded = urllib.parse.quote(str(path), safe="/:")
        conn = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True, timeout=3.0)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None
```
```python
# after
import argparse
import sqlite3

from cccs_hooks.cache import _db_path
from cc_session_tools.lib.db import connect as _sqlite_connect


def _connect_readonly() -> sqlite3.Connection | None:
    try:
        return _sqlite_connect(_db_path(), readonly=True)
    except sqlite3.Error:
        return None
```

`from pathlib import Path` and `import urllib.parse` are both deleted — `Path` is no longer
referenced anywhere else in this file (confirm with a search before deleting: `grep -n Path
src/cccs_hooks/stats.py` should show zero remaining hits other than the import line itself), and
`urllib.parse` was only used for the manual URI-quoting this change removes.

**Known limitation, documented not fixed here:** `lib.db.connect()`'s readonly branch builds its
`file:` URI as `f"file:{path}?mode=ro"` without URL-encoding — unlike the old code's
`urllib.parse.quote(str(path), safe="/:")`. If `_db_path()` ever resolves to a path containing a
character with special meaning in a `file:` URI (a literal `?`, `#`, or a space), the raw f-string
form could misparse. In practice `data_home()` defaults to `~/.local/share/claude/command-cache.db`
(no such characters) and `CCCS_CACHE_DB` is operator-controlled, so this is inert today. Fixing it
means changing `lib/db.py`'s shared `connect()` — out of scope for this phase (Phase 1's module is
shared infra consumed by Phases 2-6; changing its contract mid-migration risks all of them). Note
this as a candidate follow-up for Phase 7's cleanup pass rather than silently dropping the
protection this file used to have.

- [ ] **Step 4: Run the new test plus the full stats-related suite**

Run: `uv run pytest tests/test_cache_sqlite.py -v`
Expected: all PASS, including `test_stats_main_no_db_found` and the pre-existing
`test_stats_main_no_crash`.

- [ ] **Step 5: Commit**

```bash
git add src/cccs_hooks/stats.py tests/test_cache_sqlite.py
git commit -m "refactor(stats): use shared lib.db.connect(readonly=True) helper"
```

---

### Task 4: `claude_flags.py` — introduce `CCST_CLAUDE_FLAGS_DIR` and relocate to `data_home()`

**Files:**
- Modify: `src/cc_session_tools/lib/claude_flags.py:1-11` (imports, constants)
- Modify: `tests/test_claude_flags.py` (all three existing tests' redirection mechanism)

- [ ] **Step 1: Write the failing tests**

Replace the top of `tests/test_claude_flags.py` (imports unchanged; each test's `monkeypatch.setattr`
calls become `monkeypatch.setenv`):

```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import cc_session_tools.lib.claude_flags as cf


SAMPLE_HELP = """
Usage: claude [options]

Options:
  --model <model>   Model to use
  --debug           Enable debug
  -p, --print       Print and exit
  --append-system-prompt <p>  Append system prompt
  -h, --help        Display help
"""


def test_get_claude_flags_parses_long_flags(tmp_path, monkeypatch):
    # Use a real file so Path.stat() works — avoids mock interference with mkdir(exist_ok=True)
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/bash")
    monkeypatch.setenv("CCST_CLAUDE_FLAGS_DIR", str(tmp_path))
    with patch("shutil.which", return_value=str(fake_claude)), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=SAMPLE_HELP, stderr="", returncode=0)
        flags = cf.get_claude_flags()
    assert "--model" in flags
    assert "--debug" in flags
    assert "--append-system-prompt" in flags
    assert "--help" in flags
    assert "-p" not in flags  # short flags excluded


def test_get_claude_flags_uses_cache(tmp_path, monkeypatch):
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/bash")
    real_mtime = fake_claude.stat().st_mtime
    monkeypatch.setenv("CCST_CLAUDE_FLAGS_DIR", str(tmp_path))
    cache_file = tmp_path / "claude-flags.json"
    cache_data = {"mtime": real_mtime, "path": str(fake_claude), "flags": ["--model", "--debug"]}
    cache_file.write_text(json.dumps(cache_data))
    with patch("shutil.which", return_value=str(fake_claude)), \
         patch("subprocess.run") as mock_run:
        flags = cf.get_claude_flags()
        mock_run.assert_not_called()
    assert "--model" in flags


def test_get_claude_flags_returns_empty_if_claude_missing(monkeypatch):
    with patch("shutil.which", return_value=None):
        flags = cf.get_claude_flags()
    assert flags == set()


def test_default_cache_dir_uses_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("CCST_CLAUDE_FLAGS_DIR", raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    assert cf._cache_file() == tmp_path / "claude-flags.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_claude_flags.py -v`
Expected: FAIL — `AttributeError: module 'cc_session_tools.lib.claude_flags' has no attribute
'_cache_file'` (new test), and the first two tests fail because `CCST_CLAUDE_FLAGS_DIR` isn't read
yet — flags end up written to/read from the real default location instead of `tmp_path`, so
`mock_run.assert_not_called()` in the second test fails (cache miss against the wrong path).

- [ ] **Step 3: Write the implementation**

Replace lines 1-11 of `src/cc_session_tools/lib/claude_flags.py`:

```python
# before
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


_CACHE_DIR: Path = Path.home() / ".cache" / "cc-session-tools"
_CACHE_FILE: Path = _CACHE_DIR / "claude-flags.json"
```
```python
# after
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from cc_session_tools.lib.paths import data_home

_CLAUDE_FLAGS_DIR_ENV = "CCST_CLAUDE_FLAGS_DIR"


def _cache_dir() -> Path:
    env = os.environ.get(_CLAUDE_FLAGS_DIR_ENV, "").strip()
    return Path(env) if env else data_home()


def _cache_file() -> Path:
    return _cache_dir() / "claude-flags.json"
```

Update the two remaining references inside `get_claude_flags()` (the mkdir/write block is finished
in Task 5 — for this step, only the *read* path changes):

```python
# before (line 29)
    if _CACHE_FILE.exists():
        try:
            cached = json.loads(_CACHE_FILE.read_text())
```
```python
# after
    cache_file = _cache_file()
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
```

(The `cache_file` local variable is reused by the write block in Task 5, avoiding a second call to
`_cache_file()` later in the same function.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_claude_flags.py -v`
Expected: PASS (4 tests). Note: the write path (`_CACHE_DIR.mkdir` / `_CACHE_FILE.write_text`)
still references the old constant names at this point — the module won't fully import correctly
until Task 5 replaces them too. **Do this step and Task 5's Step together if `_CACHE_DIR`/
`_CACHE_FILE` no longer exist as names after this step's edit** — since this plan deletes both
constants in this same Step 3, the write block further down the function must be updated in the
same edit (Task 5 below shows the write-block diff; apply both diffs from this task and Task 4's
Step 3 in one commit if your editor session processes the file top-to-bottom, since a half-edited
file with `_CACHE_DIR`/`_CACHE_FILE` deleted but still referenced later won't run). In practice:
finish Task 5's Step 3 edit before running this task's Step 4 test.

- [ ] **Step 5: Commit** (after Task 5's implementation step is also complete — see note above)

```bash
git add src/cc_session_tools/lib/claude_flags.py tests/test_claude_flags.py
git commit -m "feat(claude-flags): relocate cache to data_home() via CCST_CLAUDE_FLAGS_DIR"
```

---

### Task 5: `claude_flags.py` — fix non-atomic write via `write_json_atomic()`

**Files:**
- Modify: `src/cc_session_tools/lib/claude_flags.py:51-57` (write block)
- Test: `tests/test_claude_flags.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_flags.py`:

```python
def test_get_claude_flags_writes_cache_atomically(tmp_path, monkeypatch):
    """Regression test for the non-atomic write bugfix: no leftover .tmp file, valid content."""
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/bash")
    monkeypatch.setenv("CCST_CLAUDE_FLAGS_DIR", str(tmp_path))
    with patch("shutil.which", return_value=str(fake_claude)), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=SAMPLE_HELP, stderr="", returncode=0)
        cf.get_claude_flags()

    cache_file = tmp_path / "claude-flags.json"
    assert cache_file.exists()
    assert not cache_file.with_suffix(".tmp").exists()
    written = json.loads(cache_file.read_text())
    assert "--model" in written["flags"]
    assert written["path"] == str(fake_claude)
```

- [ ] **Step 2: Run test to verify it fails or passes as a baseline**

Run: `uv run pytest tests/test_claude_flags.py::test_get_claude_flags_writes_cache_atomically -v`
Expected: this test can PASS even against the pre-fix direct-`write_text()` implementation (a
single-threaded, non-crashing test run has no way to observe a truncated write) — it exists as a
regression guard for the *shape* of the write, not a proof the old code was broken. Confirm it
passes, then proceed to the implementation change and re-run to confirm it still passes afterward
(this is the one step in this plan where "was already passing" is the expected and correct
outcome, since the underlying race is not deterministically reproducible in a unit test — the fix
is justified by code inspection per Decision in the task brief, not by a failing test).

- [ ] **Step 3: Write the implementation**

Add the import:

```python
from cc_session_tools.hooks_install import write_json_atomic
```

Replace the write block:

```python
# before (lines 51-57)
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"mtime": mtime, "path": claude, "flags": sorted(flags)})
        )
    except OSError:
        pass
```
```python
# after
    try:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            cache_file,
            {"mtime": mtime, "path": claude, "flags": sorted(flags)},
        )
    except OSError:
        pass
```

`cache_file` here is the same local variable introduced in Task 4's Step 3 read-path edit (`cache_file
= _cache_file()`, computed once near the top of the function) — reused rather than calling
`_cache_file()` a second time. `write_json_atomic()` (`cc_session_tools/hooks_install.py:69-72`)
writes to `path.with_suffix(".tmp")` then `tmp.replace(path)`, which is atomic on POSIX (`rename(2)`)
— a crash or concurrent read mid-write can no longer observe a truncated file. Note this changes the
on-disk JSON formatting from compact (`json.dumps(...)` with no `indent`) to pretty-printed
(`write_json_atomic` uses `indent=2`) — a cosmetic difference only, since the file is always read
back via `json.loads()`, never diffed or hand-edited.

- [ ] **Step 4: Run full test file to verify all pass**

Run: `uv run pytest tests/test_claude_flags.py -v`
Expected: PASS (5 tests total: 3 original + `test_default_cache_dir_uses_data_home` +
`test_get_claude_flags_writes_cache_atomically`)

- [ ] **Step 5: Check `ccr`'s consumer test still passes**

`src/cc_session_tools/cli/ccr.py` imports `get_claude_flags` (function only, no constants) and
`tests/test_cli_ccr.py` monkeypatches the function itself (`monkeypatch.setattr(cf,
"get_claude_flags", ...)`), not any of the relocated internals — this should be unaffected, but
confirm:

Run: `uv run pytest tests/test_cli_ccr.py -v`
Expected: PASS, no changes needed to this file.

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/lib/claude_flags.py tests/test_claude_flags.py
git commit -m "fix(claude-flags): write cache atomically via write_json_atomic()"
```

---

## Verification

- [ ] **Run the full test suite to confirm no regressions**

```bash
uv run pytest -q
```

Expected: all tests pass, including the 3 new tests in `test_cache_sqlite.py`
(`test_default_db_path_uses_data_home`, `test_stats_main_no_db_found`, plus the pre-existing suite
unchanged) and the 2 new tests in `test_claude_flags.py` (`test_default_cache_dir_uses_data_home`,
`test_get_claude_flags_writes_cache_atomically`).

- [ ] **Confirm CI's actual check matches** — as of this writing, `.github/workflows/ci.yml` runs
  `uv run pytest -q` and a separate `uv tool install .` smoke test (`ccd --version` etc. — none of
  which touch these two files); there is no `ruff` config in this repo and `mypy` (a `dev` extras
  dependency) is not wired into CI. If either has been added since this plan was written, run
  whatever `pyproject.toml`/CI now specifies before considering this phase done. Best-effort, not
  CI-gated as of today:

```bash
uv run mypy src/cccs_hooks/cache.py src/cccs_hooks/stats.py src/cc_session_tools/lib/claude_flags.py
```

- [ ] **Manual smoke check (optional but cheap): confirm the relocated cache actually gets created**

```bash
CCST_DATA_HOME=/tmp/ccst-smoke-check uv run python -c "
from cccs_hooks.cache import cache_record, cache_lookup, sha256_command
sha = sha256_command('echo hello')
cache_record(sha, 'safe', 'none', 'echo hello')
print(cache_lookup(sha))
"
ls /tmp/ccst-smoke-check/command-cache.db
rm -rf /tmp/ccst-smoke-check
```

Expected: prints a `CacheEntry(...)` with `verdict='safe'`; the `.db` file exists at the new
root-relative location, not under `~/.cache/claude/logs/`.

## Handoff

Phase 6 is complete when both stores are relocated under `data_home()` (`command-cache.db` and
`stats.py`'s reader via the shared `lib.db.connect()` helper; `claude-flags.json` via the new
`CCST_CLAUDE_FLAGS_DIR` env var and an atomic write), all five tasks are committed individually,
and the full test suite passes. No migration script exists for either store (Decisions 3 and 5) —
this is intentional, not a gap. Phase 7's cleanup pass should fold the two manual `rm`
one-liners above into whatever doctor/gc-report messaging it adds for orphaned pre-migration files
from the other phases, and should track the `lib/db.py` readonly-URI-encoding limitation noted in
Task 3 as a candidate follow-up.
