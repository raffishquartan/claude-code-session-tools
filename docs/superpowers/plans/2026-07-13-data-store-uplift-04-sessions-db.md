# Phase 4: `sessions.db` — tag cache + activity sentinels + doctor-mutes + `ccl`/`ccr` rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Read `2026-07-13-data-store-uplift-00-overview.md` and `2026-07-13-data-store-uplift-01-shared-infra.md`
> first — they fix the env-var conventions, `lib/paths.py`/`lib/db.py` connection-helper contract,
> and migration-script safety rules this plan builds on. This phase depends on Phase 1 (shared
> infra) being merged; see Task 0.

**Goal:** replace three independent flat-file stores — the `.tag` UUID→name-tag cache, the
`.last-opened`/`.last-active` per-session activity sentinels, and `~/.claude/cc-doctor-mutes.json`
— with one SQLite store (`sessions.db`), and rewrite `ccl --global`/`ccr`'s session enumeration and
matching to query it instead of walking the filesystem on every invocation. Retire the now-obsolete
`ccst tags migrate` one-shot tool in favour of a new `ccst sessions migrate` that populates
`sessions.db` from all three legacy sources at once.

**Architecture:** one new library module, `src/cc_session_tools/lib/sessions_db.py`, owns the
schema (three tables: `session_tags`, `sessions`, `doctor_mutes`) and every read/write query,
opened through Phase 1's `cc_session_tools.lib.db.connect()` helper. Three existing hooks/CLIs
become thin callers of that module: `cccs_hooks/session_tag.py` (writer), `cccs_hooks/after_response.py`
(writer), `cc_session_tools/lib/doctor_mutes.py` (reader/writer, signatures unchanged). `ccs.py`,
`ccr.py`, and `ccd.py` are rewritten to read/write the `sessions` table instead of walking
`cc-sessions/` directory trees or stat-ing sentinel files per session.

**Tech Stack:** Python 3.11 stdlib (`sqlite3`, `pathlib`, `argparse`, `tarfile`), pytest, `monkeypatch`.

---

## Design decisions (binding on every task below — read before writing code)

These are the explicit calls the task brief required this plan to make. Each is referenced by tag
(D1, D2, ...) from the tasks that implement it.

### D1 — `--order-by update`'s `rglob` walk is NOT replaced by a cached DB column

`_get_session_update_mtime()` (today's "worst offender") computes the newest mtime of *any* file
anywhere under a session's `working/`/`out/` tree. That tree changes continuously while a session
is live, as the direct result of Edit/Write tool calls the agent makes mid-session — not at
SessionStart or Stop. Keeping a DB column for it accurately would require a **new** hook (e.g.
PostToolUse, firing on every file-touching tool call) to keep it fresh. That is out of this phase's
consolidation list (session-tag cache + activity sentinels + doctor-mutes — not a new hook), and a
periodically-stale cached value would silently misorder `--order-by update` results, which is worse
than today's always-correct-but-slow behaviour.

**Decision:** `_get_session_update_mtime()` keeps doing its `rglob("*")` walk, unchanged, in Task 10.
What *does* change is what it walks: today it runs once per session in a candidate set built by an
O(roots × projects × sessions) filesystem walk (items 1+3 below); after this phase, the candidate
set comes from one indexed `sessions.db` query. The walk itself is not accelerated, but it is never
run against sessions outside the current filter scope, exactly matching today's behaviour — no
regression, and the two genuinely fixable bottlenecks (enumeration, and the opened/active sentinel
stats) are fixed.

### D2 — orphan-transcript indexing (`find_orphan_transcripts`, `ccr --include-orphans`) stays file-based, deferred

Indexing orphans would mean parsing `custom-title` records out of **every** JSONL transcript under
every `~/.claude/projects/<encoded>/` directory, including transcripts that never got a `cc-sessions/`
directory at all — a different, larger problem (full transcript indexing) than this phase's three
named stores. `find_orphan_transcripts()` is untouched by this plan; it keeps walking
`~/.claude/projects/` and calling `claude_code_usage.session_names.load_jsonl_titles()` exactly as
it does today. This is a documented future extension point, not a regression: `--include-orphans`
was already the slow, opt-in path before this phase.

### D3 — `claude_code_usage.session_names.py`'s harness-PID persistent cache is NOT absorbed into `sessions.db`

`session_names.py` lives in a different package (`claude_code_usage`, not `cc_session_tools`),
serves a different caller (usage/cost reporting, not `ccd`/`ccr`/`ccs`), and its priority-merge
algorithm (live PID file > JSONL `custom-title` > cache > fallback) depends on the same
full-transcript JSONL scan deferred in D2. `ccst gc report` today explicitly does not report on this
store either, confirming there is no existing integration expectation to preserve. Merging it in now
would conflate two different naming systems (the ccd/ccr-assigned *tag* vs. the harness's live
`claude -n` *display name*) around a shared schema mid-migration. Left untouched by this phase.

### D4 — `ccst tags migrate` is retired; `ccst sessions migrate` replaces it

`migrate_session_tags.py`'s only job was copying `.tag` files from the old
`~/.claude/projects/**/*.tag` location into the flat `~/.cache/claude/session-tags/` cache — a cache
this phase deletes. It has zero test coverage today and zero other callers
(confirmed by repo-wide grep in this plan's research). Task 14 deletes the module, its CLI wiring,
and its help text, replacing it with `ccst sessions migrate` (Task 13), which performs the same kind
of one-shot, non-destructive, dry-run-capable copy, but sources from all three legacy stores (tag
cache, activity sentinels, doctor-mutes JSON) into `sessions.db`.

### D5 — `find_jsonl_for_session`'s Strategy 1b (legacy in-transcript-dir `.tag` files) is dropped

`sessions.py`'s own docstring already flags Strategy 1b ("old-style `.tag` files still in
transcript_dir") as "backward-compat fallback... can be removed after migration is confirmed
complete" — it was itself a leftover from the *previous* migration (pre-flat-cache to flat-cache).
This phase's migration (Task 13) is exactly the natural point to remove it: any legitimate old-style
file would already have been swept up by the now-retired `ccst tags migrate` if it was going to be;
anything still stray is stale, and Strategy 2 (JSONL `custom-title` scan, unchanged, file-based) is
the correct fallback for genuinely untagged transcripts. Task 7 removes Strategy 1b's code.

### D6 — stale-row guard lives only on `ccr`'s *final* single match, not during enumeration/matching

Switching `ccr`'s matching from a filesystem walk (which can only ever return directories that
exist) to a `sessions.db` query (which can return rows for directories deleted outside of any
DB-aware tool, e.g. `rm -rf cc-sessions/foo`) introduces one narrow regression: a stale row could be
"resumed" into a nonexistent directory. Re-adding an `is_dir()` check to every row during matching
would reintroduce exactly the O(n) stat-call cost this phase removes. Instead, Task 12 adds a single
`is_dir()` check only on the one row `ccr` is about to actually launch into (after picker selection
or the single-match path) — O(1), and gives a clean "stale index entry" error instead of a broken
launch. `sessions.db` rows are never deleted automatically in this phase (no GC job); this is an
accepted, documented trade-off, not a silent gap — a stale row degrades gracefully to a clear error
message, it does not corrupt or crash anything.

### D7 — `ccst doctor --mutes-file <PATH>` now means "sqlite db file", not "JSON file"

Per the task brief, doctor-mutes consolidates into `sessions.db` (design-spec's root-B "durable
acknowledgement record" category). `doctor_mutes.py`'s four function signatures
(`default_mutes_path() -> Path`, `load_mutes(path)`, `add_mute(path, name, *, today)`,
`remove_mute(path, name)`) are preserved byte-for-byte (Task 3) — `ccst.py`'s `_cmd_doctor` needs
**zero** code changes, satisfying "preserve this interface exactly." The one visible change is that
`--mutes-file` (and the default it falls back to) now names a `.db` file, not a `.json` file; the
flag's help text is updated in Task 14 to say so.

### D8 — `~/.mcp-servers-last-security-review` — confirmed out of scope, no action

Repeats `overview.md`'s own finding: zero code in this repo touches this file. Nothing to migrate.

### D9 — `ccst gc report` — extension point noted, not implemented this phase

`session_gc.py`'s `known_session_uuids()` is itself a `projects_dir.glob("*/*.jsonl")` walk that
could be replaced by a `sessions.db`-backed query in the future, and `sessions.db`'s own orphaned
rows (D6) are a candidate for a fifth `StoreReport`. Per the brief, this is flagged as an extension
point for Phase 7 or later, not implemented here — no changes to `session_gc.py` in this plan.

---

## File Structure

- Create: `src/cc_session_tools/lib/sessions_db.py`
- Create: `src/cc_session_tools/cli/migrate_sessions_db.py`
- Modify: `src/cc_session_tools/lib/doctor_mutes.py` (rewrite body, signatures unchanged)
- Modify: `src/cccs_hooks/session_tag.py`
- Modify: `src/cccs_hooks/after_response.py`
- Modify: `src/cc_session_tools/lib/sessions.py` (`find_jsonl_for_session`, `find_matching_sessions`;
  remove `_session_tags_dir`, `DEFAULT_SESSION_TAGS_DIR`)
- Modify: `src/cc_session_tools/cli/ccd.py`
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `src/cc_session_tools/cli/ccr.py`
- Modify: `src/cc_session_tools/cli/ccst.py` (remove `tags migrate`, add `sessions migrate`/`sessions list`)
- Delete: `src/cc_session_tools/cli/migrate_session_tags.py`
- Create: `tests/test_sessions_db.py`
- Create: `tests/test_doctor_mutes.py`
- Create: `tests/test_migrate_sessions_db.py`
- Create: `tests/test_ccst_sessions_cli.py`
- Modify: `tests/test_session_tag.py`, `tests/test_after_response.py`, `tests/test_empty_session.py`,
  `tests/test_sessions.py`, `tests/test_cli_ccd.py`, `tests/test_ccs_sentinel_sort.py`,
  `tests/test_ccs_session_counts.py`, `tests/test_ccs_emptiness.py`, `tests/test_ccs_scope_flags.py`,
  `tests/test_ccs_list_mode.py`, `tests/test_cli_ccs.py`, `tests/test_ccr_orphans.py`,
  `tests/test_cli_ccr.py`, `tests/test_ccst_doctor.py`
- Delete: none (no test file exists for `migrate_session_tags.py` — confirmed zero coverage)

---

## Task 0: Confirm Phase 1 prerequisites are present

**Files:** none (read-only check)

- [ ] **Step 1: Verify `lib/paths.py` and `lib/db.py` exist and export the fixed contract**

```bash
uv run python -c "
from cc_session_tools.lib import paths, db
assert callable(paths.data_home)
assert callable(db.connect) and callable(db.checkpoint) and callable(db.backup_to)
print('Phase 1 contract present')
"
```

Expected: `Phase 1 contract present`. If this fails with `ImportError`, **stop** — implement Phase 1
(`2026-07-13-data-store-uplift-01-shared-infra.md`) first; this plan imports both modules starting
in Task 1.

- [ ] **Step 2: Confirm branch state**

```bash
git status
git log --oneline -3
```

Expected: working tree clean apart from this plan's own new files; branch history includes Phase 1's
commits (`lib/paths.py`, `lib/db.py`).

---

## Task 1: `lib/sessions_db.py` — schema, path resolution, `connect()`, `session_tags` functions

**Files:**
- Create: `src/cc_session_tools/lib/sessions_db.py`
- Test: `tests/test_sessions_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sessions_db.py
"""Tests for cc_session_tools.lib.sessions_db — the sessions.db store."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cc_session_tools.lib import sessions_db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.db"


# ---------- default_db_path / env override ----------

def test_default_db_path_honours_ccst_sessions_dir_env(tmp_path, monkeypatch):
    override = tmp_path / "custom-dir"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(override))
    assert sessions_db.default_db_path() == override / "sessions.db"


def test_default_db_path_falls_back_to_data_home(tmp_path, monkeypatch):
    monkeypatch.delenv("CCST_SESSIONS_DIR", raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    assert sessions_db.default_db_path() == tmp_path / "data-home" / "sessions.db"


# ---------- session_tags ----------

def test_write_tag_then_lookup_returns_tag(db_path):
    sessions_db.write_tag("uuid-1", "my-feature", path=db_path)
    result = sessions_db.lookup_tags(["uuid-1"], path=db_path)
    assert result == {"uuid-1": "my-feature"}


def test_lookup_tags_returns_empty_dict_for_unknown_uuids(db_path):
    sessions_db.write_tag("uuid-1", "my-feature", path=db_path)
    result = sessions_db.lookup_tags(["uuid-2", "uuid-3"], path=db_path)
    assert result == {}


def test_lookup_tags_batches_multiple_uuids(db_path):
    sessions_db.write_tag("uuid-1", "tag-one", path=db_path)
    sessions_db.write_tag("uuid-2", "tag-two", path=db_path)
    result = sessions_db.lookup_tags(["uuid-1", "uuid-2", "uuid-missing"], path=db_path)
    assert result == {"uuid-1": "tag-one", "uuid-2": "tag-two"}


def test_lookup_tags_empty_list_returns_empty_dict_without_opening_db(db_path):
    # db_path does not exist yet — must not raise.
    assert sessions_db.lookup_tags([], path=db_path) == {}
    assert not db_path.exists()


def test_lookup_tags_on_nonexistent_db_returns_empty_dict(db_path):
    # No writer has ever run — readonly connect() would raise OperationalError;
    # lookup_tags must degrade gracefully instead of propagating it.
    assert not db_path.exists()
    assert sessions_db.lookup_tags(["uuid-1"], path=db_path) == {}


def test_write_tag_upserts_on_conflict(db_path):
    sessions_db.write_tag("uuid-1", "old-tag", path=db_path)
    sessions_db.write_tag("uuid-1", "new-tag", path=db_path)
    assert sessions_db.lookup_tags(["uuid-1"], path=db_path) == {"uuid-1": "new-tag"}


def test_write_tag_creates_db_file(db_path):
    assert not db_path.exists()
    sessions_db.write_tag("uuid-1", "my-feature", path=db_path)
    assert db_path.exists()


def test_schema_has_three_tables(db_path):
    sessions_db.write_tag("uuid-1", "my-feature", path=db_path)  # bootstrap schema
    conn = sqlite3.connect(str(db_path))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"session_tags", "sessions", "doctor_mutes"} <= names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.sessions_db'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/sessions_db.py
"""sessions.db — consolidates the session-tag cache, per-session activity
sentinels (.last-opened / .last-active), and the doctor drift-mute store into
one SQLite file under paths.data_home().

Replaces three flat-file stores:
  - ~/.cache/claude/session-tags/<uuid>.tag           -> session_tags table
  - cc-sessions/<basename>/.last-opened, .last-active  -> sessions table
  - ~/.claude/cc-doctor-mutes.json                     -> doctor_mutes table
    (doctor_mutes.py stays the public-facing module for that table; it
    imports DDL/default_db_path/connect from here so all three tables share
    one schema and one file.)

Every read/write opens a connection via connect(), which delegates to the
Phase 1 shared helper cc_session_tools.lib.db.connect() (WAL mode, busy
timeout, dict-style rows). Connections are opened and closed per call,
matching the existing per-call pattern in cccs_hooks/cache.py — WAL mode is
specifically designed for many short-lived writers from different processes
(hooks fire once per SessionStart/Stop event), so this needs no pooling.
"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import db, paths

SESSIONS_DIR_ENV = "CCST_SESSIONS_DIR"
_DB_FILENAME = "sessions.db"

DDL = """
CREATE TABLE IF NOT EXISTS session_tags (
    uuid       TEXT PRIMARY KEY,
    tag        TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    project_dir   TEXT NOT NULL,
    basename      TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    last_opened   REAL,
    last_active   REAL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (project_dir, basename)
);
CREATE INDEX IF NOT EXISTS idx_sessions_basename    ON sessions(basename);
CREATE INDEX IF NOT EXISTS idx_sessions_start_date  ON sessions(start_date);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions(last_active);
CREATE INDEX IF NOT EXISTS idx_sessions_last_opened ON sessions(last_opened);

CREATE TABLE IF NOT EXISTS doctor_mutes (
    name     TEXT PRIMARY KEY,
    muted_at TEXT NOT NULL
);
"""


def default_db_path() -> Path:
    """sessions.db location. Overridable via CCST_SESSIONS_DIR (a directory);
    falls back to paths.data_home()."""
    override = os.environ.get(SESSIONS_DIR_ENV)
    base = Path(override) if override else paths.data_home()
    return base / _DB_FILENAME


def connect(*, path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    """Open sessions.db (or an explicit override path — used by tests and by
    ccst doctor --mutes-file). readonly=True skips schema creation; callers
    that only read must handle sqlite3.OperationalError for a not-yet-created
    file (see lookup_tags/list_sessions/find_exact for the established
    graceful-degradation pattern)."""
    target = path if path is not None else default_db_path()
    if readonly:
        return db.connect(target, readonly=True)
    return db.connect(target, ddl=DDL)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# session_tags
# ---------------------------------------------------------------------------

def write_tag(uuid: str, tag: str, *, path: Path | None = None) -> None:
    """Record (or update) the tag for a session uuid."""
    conn = connect(path=path)
    try:
        conn.execute(
            "INSERT INTO session_tags (uuid, tag, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(uuid) DO UPDATE SET tag=excluded.tag, updated_at=excluded.updated_at",
            (uuid, tag, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def lookup_tags(uuids: list[str], *, path: Path | None = None) -> dict[str, str]:
    """Batch uuid -> tag lookup. Returns {} for an empty input list (without
    opening a connection) and for a sessions.db that has never been written
    to (no writer has run yet — not an error condition for a reader)."""
    if not uuids:
        return {}
    try:
        conn = connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return {}
    try:
        placeholders = ",".join("?" for _ in uuids)
        rows = conn.execute(
            f"SELECT uuid, tag FROM session_tags WHERE uuid IN ({placeholders})",
            uuids,
        ).fetchall()
        return {r["uuid"]: r["tag"] for r in rows}
    finally:
        conn.close()
```

Note: the `f"...IN ({placeholders})"` string interpolates only a fixed sequence of `?`
placeholders (never uuid values themselves, which are always bound parameters) — this is the
standard sqlite3 pattern for a variable-length `IN` clause, not a SQL-injection risk.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_db.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/sessions_db.py tests/test_sessions_db.py
git commit -m "feat(sessions-db): add sessions_db.py — schema + session_tags read/write"
```

---

## Task 2: `lib/sessions_db.py` — `sessions` table functions + `SessionRow`

**Files:**
- Modify: `src/cc_session_tools/lib/sessions_db.py`
- Modify: `tests/test_sessions_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_sessions_db.py

# ---------- sessions table ----------

def test_ensure_session_row_inserts_new_row(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-my-feature", path=db_path)
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].basename == "20260713-my-feature"
    assert rows[0].project_dir == proj
    assert rows[0].start_date == "20260713"
    assert rows[0].last_opened == 0.0
    assert rows[0].last_active == 0.0


def test_ensure_session_row_is_idempotent_and_does_not_clobber_timestamps(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-my-feature", path=db_path, when=1000.0)
    sessions_db.ensure_session_row(proj, "20260713-my-feature", path=db_path)
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].last_opened == 1000.0


def test_ensure_session_row_rejects_non_session_basename(db_path):
    sessions_db.ensure_session_row(Path("/repos/myproj"), "not-a-session-name", path=db_path)
    assert sessions_db.list_sessions(path=db_path) == []


def test_touch_last_opened_sets_column(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-foo", path=db_path, when=1234.5)
    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].last_opened == 1234.5
    assert rows[0].last_active == 0.0


def test_touch_last_active_sets_column(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_active(proj, "20260713-foo", path=db_path, when=5678.5)
    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].last_active == 5678.5
    assert rows[0].last_opened == 0.0


def test_touch_last_opened_defaults_to_now_when_no_when_given(db_path):
    before = time.time()
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-foo", path=db_path)
    after = time.time()
    rows = sessions_db.list_sessions(path=db_path)
    assert before <= rows[0].last_opened <= after


def test_touch_last_opened_updates_existing_row_in_place(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-foo", path=db_path, when=100.0)
    sessions_db.touch_last_opened(proj, "20260713-foo", path=db_path, when=200.0)
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].last_opened == 200.0


def test_list_sessions_scoped_to_project_dir(db_path):
    a = Path("/repos/proj-a")
    b = Path("/repos/proj-b")
    sessions_db.ensure_session_row(a, "20260713-in-a", path=db_path)
    sessions_db.ensure_session_row(b, "20260713-in-b", path=db_path)
    rows = sessions_db.list_sessions(project_dir=a, path=db_path)
    assert [r.basename for r in rows] == ["20260713-in-a"]


def test_list_sessions_empty_db_returns_empty_list(db_path):
    assert not db_path.exists()
    assert sessions_db.list_sessions(path=db_path) == []


def test_list_sessions_limit_returns_most_recent_n_by_last_active(db_path):
    """'Most recent N' must be an indexed ORDER BY ... LIMIT, not fetch-all-then-slice —
    this is the 2026-07-13 design-spec requirement. Seed enough rows that a naive
    Python-side sort+slice would still pass, then assert exactly `limit` rows come back
    in the right order, proving the LIMIT clause itself is doing the work."""
    proj = Path("/repos/myproj")
    for i in range(20):
        sessions_db.ensure_session_row(proj, f"20260713-sess-{i:02d}", path=db_path)
        sessions_db.touch_last_active(proj, f"20260713-sess-{i:02d}", path=db_path, when=float(i))
    rows = sessions_db.list_sessions(order_by="last_active", limit=5, path=db_path)
    assert len(rows) == 5
    assert [r.basename for r in rows] == [
        "20260713-sess-19", "20260713-sess-18", "20260713-sess-17",
        "20260713-sess-16", "20260713-sess-15",
    ]


def test_list_sessions_limit_larger_than_row_count_returns_all(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-only", path=db_path)
    rows = sessions_db.list_sessions(order_by="last_active", limit=100, path=db_path)
    assert len(rows) == 1


def test_list_sessions_rejects_limit_without_order_by(db_path):
    with pytest.raises(ValueError, match="order_by"):
        sessions_db.list_sessions(limit=5, path=db_path)


def test_list_sessions_rejects_unknown_order_by_column(db_path):
    with pytest.raises(ValueError, match="order_by"):
        sessions_db.list_sessions(order_by="start_date", path=db_path)  # not DB-orderable — see docstring


def test_find_exact_matches_basename(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-exact-match", path=db_path)
    rows = sessions_db.find_exact("20260713-exact-match", path=db_path)
    assert len(rows) == 1
    assert rows[0].project_dir == proj


def test_find_exact_no_match_returns_empty_list(db_path):
    sessions_db.ensure_session_row(Path("/repos/p"), "20260713-a", path=db_path)
    assert sessions_db.find_exact("20260713-b", path=db_path) == []


def test_find_exact_on_nonexistent_db_returns_empty_list(db_path):
    assert not db_path.exists()
    assert sessions_db.find_exact("20260713-a", path=db_path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions_db.py -v`
Expected: FAIL — `AttributeError: module 'cc_session_tools.lib.sessions_db' has no attribute 'ensure_session_row'`

- [ ] **Step 3: Write the implementation**

Append to `src/cc_session_tools/lib/sessions_db.py`:

```python
# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SessionRow:
    project_dir: Path
    basename: str
    start_date: str
    last_opened: float
    last_active: float


def _row_to_session(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        project_dir=Path(row["project_dir"]),
        basename=row["basename"],
        start_date=row["start_date"],
        last_opened=row["last_opened"] or 0.0,
        last_active=row["last_active"] or 0.0,
    )


def ensure_session_row(project_dir: Path, basename: str, *, path: Path | None = None) -> None:
    """Insert a row for (project_dir, basename) if absent. Never overwrites an
    existing row's timestamps — this is the safety-net call ccd.py makes right
    after creating a session directory, in case the SessionStart hook never
    fires (hooks disabled/broken); the hook's own touch_last_opened() upsert
    is the normal path and would create the same row moments later regardless."""
    from cc_session_tools.lib.sessions import session_start_date

    start_date = session_start_date(basename)
    if start_date is None:
        return
    conn = connect(path=path)
    try:
        conn.execute(
            "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename) DO NOTHING",
            (str(project_dir), basename, start_date, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def touch_last_opened(
    project_dir: Path, basename: str, *, path: Path | None = None, when: float | None = None
) -> None:
    """Upsert the last_opened timestamp (epoch seconds) for (project_dir, basename)."""
    from cc_session_tools.lib.sessions import session_start_date

    start_date = session_start_date(basename)
    if start_date is None:
        return
    ts = when if when is not None else time.time()
    conn = connect(path=path)
    try:
        conn.execute(
            "INSERT INTO sessions (project_dir, basename, start_date, discovered_at, last_opened) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename) DO UPDATE SET last_opened=excluded.last_opened",
            (str(project_dir), basename, start_date, _now_iso(), ts),
        )
        conn.commit()
    finally:
        conn.close()


def touch_last_active(
    project_dir: Path, basename: str, *, path: Path | None = None, when: float | None = None
) -> None:
    """Upsert the last_active timestamp (epoch seconds) for (project_dir, basename)."""
    from cc_session_tools.lib.sessions import session_start_date

    start_date = session_start_date(basename)
    if start_date is None:
        return
    ts = when if when is not None else time.time()
    conn = connect(path=path)
    try:
        conn.execute(
            "INSERT INTO sessions (project_dir, basename, start_date, discovered_at, last_active) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(project_dir, basename) DO UPDATE SET last_active=excluded.last_active",
            (str(project_dir), basename, start_date, _now_iso(), ts),
        )
        conn.commit()
    finally:
        conn.close()


_ORDERABLE_COLUMNS = {"last_active", "last_opened"}


def list_sessions(
    *,
    project_dir: Path | None = None,
    path: Path | None = None,
    order_by: str | None = None,
    limit: int | None = None,
) -> list[SessionRow]:
    """Known sessions, optionally scoped to one project_dir.

    order_by/limit ("most recent N") push an indexed `ORDER BY <col> DESC
    LIMIT ?` into SQL when order_by is a DB-backed column (last_active /
    last_opened, both indexed - see idx_sessions_last_active/last_opened in
    the schema) - this is what makes "most recent N sessions" an O(log n)
    indexed lookup instead of fetching every row and slicing in Python.
    order_by values that need filesystem/Python-side computation (start,
    update - see ccs.py's --order-by) are NOT DB columns; callers needing
    those must pass order_by=None here and sort+slice the full result
    themselves (this is the documented, accepted exception - see D1 and the
    2026-07-13 performance requirement's explicit scoping in
    data-stores-design-spec.md Section 7.2, which only binds the title/tag-
    lookup path, not update-order's mtime walk).

    Empty list if sessions.db has never been written to, or if limit is
    given but no rows match.
    """
    if order_by is not None and order_by not in _ORDERABLE_COLUMNS:
        raise ValueError(f"order_by must be one of {_ORDERABLE_COLUMNS} or None, got {order_by!r}")
    if limit is not None and order_by is None:
        raise ValueError("limit requires order_by (an unordered LIMIT is meaningless)")

    try:
        conn = connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return []
    try:
        query = "SELECT project_dir, basename, start_date, last_opened, last_active FROM sessions"
        params: list[object] = []
        if project_dir is not None:
            query += " WHERE project_dir = ?"
            params.append(str(project_dir))
        if order_by is not None:
            # order_by is validated against _ORDERABLE_COLUMNS above (not
            # user-controlled free text) before this f-string runs, so this
            # is not a SQL-injection risk despite the interpolation.
            query += f" ORDER BY {order_by} DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()


def find_exact(basename: str, *, path: Path | None = None) -> list[SessionRow]:
    """Every row whose basename equals `basename` exactly (could be >1 if the
    same basename was created under two different project_dirs)."""
    try:
        conn = connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT project_dir, basename, start_date, last_opened, last_active "
            "FROM sessions WHERE basename = ?",
            (basename,),
        ).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_db.py -v`
Expected: PASS (26 tests total — 21 from the original schema/read/write coverage plus 5 for
`list_sessions`'s `order_by`/`limit` "most recent N" support added above)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/sessions_db.py tests/test_sessions_db.py
git commit -m "feat(sessions-db): add sessions table read/write functions"
```

---

## Task 3: `lib/doctor_mutes.py` — SQLite-backed rewrite (signatures unchanged)

Per D7: the same four function signatures, same call-site contract; only the storage format and
default location change. `ccst.py`'s `_cmd_doctor` needs no code changes as a result of this task
(its help text is updated separately in Task 14).

**Files:**
- Modify: `src/cc_session_tools/lib/doctor_mutes.py`
- Create: `tests/test_doctor_mutes.py` (zero coverage existed before this phase)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doctor_mutes.py
"""Tests for cc_session_tools.lib.doctor_mutes — now SQLite-backed (sessions.db,
doctor_mutes table). Zero test coverage existed for this module before this phase."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import doctor_mutes


@pytest.fixture
def mutes_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.db"


def test_load_mutes_empty_when_file_absent(mutes_path):
    assert not mutes_path.exists()
    assert doctor_mutes.load_mutes(mutes_path) == {}


def test_add_mute_then_load_returns_it(mutes_path):
    doctor_mutes.add_mute(mutes_path, "version:pypi", today="2026-07-13")
    assert doctor_mutes.load_mutes(mutes_path) == {"version:pypi": "2026-07-13"}


def test_add_mute_returns_full_mute_map(mutes_path):
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    result = doctor_mutes.add_mute(mutes_path, "b", today="2026-07-02")
    assert result == {"a": "2026-07-01", "b": "2026-07-02"}


def test_add_mute_overwrites_existing_date(mutes_path):
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-13")
    assert doctor_mutes.load_mutes(mutes_path) == {"a": "2026-07-13"}


def test_remove_mute_returns_true_when_present(mutes_path):
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    assert doctor_mutes.remove_mute(mutes_path, "a") is True
    assert doctor_mutes.load_mutes(mutes_path) == {}


def test_remove_mute_returns_false_when_absent(mutes_path):
    assert not mutes_path.exists()
    assert doctor_mutes.remove_mute(mutes_path, "nope") is False


def test_remove_mute_leaves_other_entries_intact(mutes_path):
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    doctor_mutes.add_mute(mutes_path, "b", today="2026-07-02")
    doctor_mutes.remove_mute(mutes_path, "a")
    assert doctor_mutes.load_mutes(mutes_path) == {"b": "2026-07-02"}


def test_default_mutes_path_matches_sessions_db_default(tmp_path, monkeypatch):
    from cc_session_tools.lib import sessions_db
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    assert doctor_mutes.default_mutes_path() == sessions_db.default_db_path()


def test_mutes_share_file_with_session_tags(mutes_path):
    """doctor_mutes and session_tags live in the same sessions.db file."""
    from cc_session_tools.lib import sessions_db
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    sessions_db.write_tag("uuid-1", "my-tag", path=mutes_path)
    assert doctor_mutes.load_mutes(mutes_path) == {"a": "2026-07-01"}
    assert sessions_db.lookup_tags(["uuid-1"], path=mutes_path) == {"uuid-1": "my-tag"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_doctor_mutes.py -v`
Expected: FAIL — `load_mutes`/`add_mute`/`remove_mute` still read/write JSON, `default_mutes_path()`
still returns `~/.claude/cc-doctor-mutes.json`; several assertions fail.

- [ ] **Step 3: Rewrite the implementation**

```python
# src/cc_session_tools/lib/doctor_mutes.py
"""Persistent mute store for ``ccst doctor --drift``.

A *mute* records that the user has acknowledged a specific doctor check (by its
stable ``name``, e.g. ``version:pypi`` or ``skill:foo``) and does not want the
drift monitor to flag it again until it is un-muted.

Backed by the doctor_mutes table in sessions.db (consolidated per the
data-store-uplift migration — see docs/superpowers/plans/2026-07-13-data-store-uplift-04-sessions-db.md
design decision D7). File I/O lives here so the check/filter/format logic in
:mod:`doctor` stays pure. Function signatures are unchanged from the pre-SQLite
version; only ``path`` now names a sqlite db file instead of a JSON file.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cc_session_tools.lib import sessions_db


def default_mutes_path() -> Path:
    """Canonical mute-store path: the shared sessions.db file."""
    return sessions_db.default_db_path()


def load_mutes(path: Path) -> dict[str, str]:
    """Return the mute map (check name -> ISO date). Empty if the store has
    never been written to."""
    try:
        conn = sessions_db.connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return {}
    try:
        rows = conn.execute("SELECT name, muted_at FROM doctor_mutes").fetchall()
        return {r["name"]: r["muted_at"] for r in rows}
    finally:
        conn.close()


def add_mute(path: Path, name: str, *, today: str) -> dict[str, str]:
    """Mute ``name`` (recording ``today`` as the mute date) and persist."""
    conn = sessions_db.connect(path=path)
    try:
        conn.execute(
            "INSERT INTO doctor_mutes (name, muted_at) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET muted_at=excluded.muted_at",
            (name, today),
        )
        conn.commit()
    finally:
        conn.close()
    return load_mutes(path)


def remove_mute(path: Path, name: str) -> bool:
    """Un-mute ``name``. Return True if it was muted, False if it was not."""
    conn = sessions_db.connect(path=path)
    try:
        cur = conn.execute("DELETE FROM doctor_mutes WHERE name = ?", (name,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_doctor_mutes.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/doctor_mutes.py tests/test_doctor_mutes.py
git commit -m "feat(sessions-db): back doctor_mutes.py with sessions.db (signatures unchanged)"
```

---

## Task 4: `ccst doctor` — net-new CLI-level tests for `--mute`/`--unmute`/`--list-mutes`/`--drift`

Per the brief: zero CLI-level test coverage exists today for these flags. `_cmd_doctor`'s body is
unchanged by Task 3 (it already calls `doctor_mutes.add_mute`/`remove_mute`/`load_mutes` with a
`Path` — only the meaning of that path changed), so this task is tests-only.

**Files:**
- Modify: `tests/test_ccst_doctor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ccst_doctor.py`:

```python
# ---------- mute / unmute / list-mutes CLI (net new — zero coverage existed) ----------

def test_mute_writes_and_list_mutes_shows_it(tmp_path: Path) -> None:
    mutes_file = tmp_path / "sessions.db"
    r1 = _run("doctor", "--mute", "version:pypi", "--mutes-file", str(mutes_file))
    assert r1.returncode == 0
    assert "Muted 'version:pypi'" in r1.stdout

    r2 = _run("doctor", "--list-mutes", "--mutes-file", str(mutes_file))
    assert r2.returncode == 0
    assert "version:pypi" in r2.stdout


def test_list_mutes_empty_reports_none(tmp_path: Path) -> None:
    mutes_file = tmp_path / "sessions.db"
    r = _run("doctor", "--list-mutes", "--mutes-file", str(mutes_file))
    assert r.returncode == 0
    assert "No checks are muted" in r.stdout


def test_unmute_removes_a_muted_check(tmp_path: Path) -> None:
    mutes_file = tmp_path / "sessions.db"
    _run("doctor", "--mute", "hook:foo", "--mutes-file", str(mutes_file))
    r = _run("doctor", "--unmute", "hook:foo", "--mutes-file", str(mutes_file))
    assert r.returncode == 0
    assert "Un-muted 'hook:foo'" in r.stdout

    r2 = _run("doctor", "--list-mutes", "--mutes-file", str(mutes_file))
    assert "hook:foo" not in r2.stdout


def test_unmute_not_muted_returns_1(tmp_path: Path) -> None:
    mutes_file = tmp_path / "sessions.db"
    r = _run("doctor", "--unmute", "never-muted", "--mutes-file", str(mutes_file))
    assert r.returncode == 1
    assert "was not muted" in r.stdout


def test_drift_mode_hides_muted_issues(tmp_path: Path) -> None:
    mutes_file = tmp_path / "sessions.db"
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    # Mute one of the checks that will definitely WARN/FAIL in a clean env.
    r_first = _run("doctor", "--drift", "--no-pypi", "--settings", str(settings))
    # Extract a real un-muted check name from the drift output to mute it.
    lines = [l for l in r_first.stdout.splitlines() if l.strip().startswith("[")]
    assert lines, "expected at least one un-muted issue to mute in this test"
    name = lines[0].split("]", 1)[1].split()[0]

    _run("doctor", "--mute", name, "--mutes-file", str(mutes_file))
    r_after = _run(
        "doctor", "--drift", "--no-pypi", "--settings", str(settings),
        "--mutes-file", str(mutes_file),
    )
    assert name not in r_after.stdout


def test_mutes_file_default_is_sessions_db_not_json(tmp_path: Path, monkeypatch) -> None:
    """Regression guard for D7: the default mute store is sessions.db, not
    the old ~/.claude/cc-doctor-mutes.json path."""
    from cc_session_tools.lib import doctor_mutes, sessions_db
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    assert doctor_mutes.default_mutes_path() == sessions_db.default_db_path()
    assert doctor_mutes.default_mutes_path().name == "sessions.db"
```

- [ ] **Step 2: Run tests to verify they fail or pass appropriately**

Run: `uv run pytest tests/test_ccst_doctor.py -v -k "mute or drift_mode_hides"`
Expected: these specific tests should already PASS at this point (Task 3 finished before this task
starts, and `_cmd_doctor`'s body was never broken) — this task exists purely to add the missing
regression coverage the brief calls out, not to fix a bug. If any fail, investigate before
proceeding; do not silently "fix" the test to match broken behaviour.

- [ ] **Step 3: Run the full doctor test file**

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: PASS (all tests, existing + 6 new)

- [ ] **Step 4: Commit**

```bash
git add tests/test_ccst_doctor.py
git commit -m "test(doctor): add CLI coverage for --mute/--unmute/--list-mutes/--drift"
```

---

## Task 5: `cccs_hooks/session_tag.py` — write to `sessions.db` instead of flat files

**Files:**
- Modify: `src/cccs_hooks/session_tag.py`
- Modify: `tests/test_session_tag.py`

- [ ] **Step 1: Rewrite the implementation**

```python
# src/cccs_hooks/session_tag.py
"""SessionStart hook: records the session tag + .last-opened activity into
sessions.db, and emits ccd/ccr session context.

When CLD_SESSION_TAG is set (i.e. the session was started via the `ccd` or
`ccr` shell wrapper), this hook does two things:

1. Records session_id -> tag in sessions.db's session_tags table, so ccs/ccr
   and other tools can map session UUIDs to the human-readable name tag
   assigned at session creation.

2. If CLD_SESSION_DIR is set and shaped like <project_dir>/cc-sessions/<basename>,
   upserts the sessions table's last_opened timestamp for that row (creating
   the row if it does not already exist — see sessions_db.touch_last_opened).

3. Emits `additionalContext` (mode-specific for CLD_SESSION_MODE=new vs
   resume) telling the assistant the tag/session-dir is already set, so it
   skips asking the user for a session name.

Runs silently (returns 0, emits nothing) when CLD_SESSION_TAG is not set
(non-ccd/ccr sessions). Never raises — write failures are reported to
stderr only and do not prevent the additionalContext from being emitted.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

from cc_session_tools.lib import sessions_db


def encode_path(path: str) -> str:
    """Encode a filesystem path to the name Claude Code uses under ~/.claude/projects/.

    Claude Code replaces every character that is not alphanumeric with '-'.
    Examples:
        /home/alice          -> -home-alice
        /home/alice/.claude  -> -home-alice--claude   (the '.' also becomes '-')
        /mnt/c/Users/alice/repos/myproject
                             -> -mnt-c-Users-alice-repos-myproject

    NOTE: encode_path() is not used for tag recording (tags are now uuid-keyed
    rows in sessions.db, not cwd-encoded paths). It is kept because its
    documented contract is tested and removing it is a separate cleanup.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def _additional_context_message(tag: str, session_dir: str, mode: str) -> str:
    """Build the mode-specific SessionStart additionalContext message.

    Ported verbatim (content-wise) from the former cc-wrapper-session-tag.sh
    in claude-code-config-sync.
    """
    if mode == "resume":
        return (
            f"Session tag is already set to `{tag}` by the ccr shell wrapper. "
            "The session is being resumed today. The session directory "
            f"`{session_dir}/` already exists. Session names reflect the start "
            "date only and are not renamed just because activity spans multiple "
            "days. Do NOT ask the user for a name tag — skip that step in the "
            "CLAUDE.md startup flow. Proceed directly to the hooks report as normal."
        )
    return (
        f"Session tag is already set to `{tag}` by the ccd shell wrapper. "
        f"The session directory `{session_dir}/` (with working/ and out/ "
        "subdirs) has already been created. The session display name has "
        "already been set via `claude -n` at startup, so /rename is "
        "unnecessary. Do NOT ask the user for a name tag — skip that step in "
        "the CLAUDE.md startup flow. Proceed directly to the hooks report as normal."
    )


def main(argv: list[str] | None = None) -> int:
    tag = os.environ.get("CLD_SESSION_TAG")
    if not tag:
        return 0

    raw = sys.stdin.read()
    try:
        data: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[session-tag] Could not parse hook payload: {exc}", file=sys.stderr)
        data = {}

    session_id = str(data.get("session_id") or "")
    if not session_id:
        print(
            f"[session-tag] session_id absent from hook payload for tag {tag!r}; "
            "tag not recorded",
            file=sys.stderr,
        )
    else:
        try:
            sessions_db.write_tag(session_id, tag)
        except (OSError, sqlite3.Error) as exc:
            print(f"[session-tag] Failed to record tag: {exc}", file=sys.stderr)

    session_dir_str = os.environ.get("CLD_SESSION_DIR", "")
    if session_dir_str:
        session_dir_path = Path(session_dir_str)
        if session_dir_path.parent.name == "cc-sessions":
            try:
                sessions_db.touch_last_opened(
                    session_dir_path.parent.parent, session_dir_path.name
                )
            except (OSError, sqlite3.Error) as exc:
                print(f"[session-tag] Failed to record .last-opened: {exc}", file=sys.stderr)

    session_dir = session_dir_str or f"cc-sessions/{date.today():%Y%m%d}-{tag}"
    mode = os.environ.get("CLD_SESSION_MODE", "new")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _additional_context_message(tag, session_dir, mode),
        }
    }))

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Rewrite the test file**

The `encode_path` tests (lines 16-31 of the original) and the `additionalContext` emission tests
(lines 264-381) are unaffected by this rewrite — they don't touch tag storage. Only the
tag-file-write and `.last-opened`-file assertions change. Replace the whole file:

```python
# tests/test_session_tag.py
"""Tests for cccs_hooks.session_tag — SessionStart hook that records tags and
.last-opened activity into sessions.db."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cccs_hooks import session_tag
from cc_session_tools.lib import sessions_db


# ---------------------------------------------------------------------------
# encode_path
# ---------------------------------------------------------------------------

def test_encode_path_replaces_slashes_with_dashes():
    assert session_tag.encode_path("/home/alice") == "-home-alice"


def test_encode_path_replaces_dots_with_dashes():
    assert session_tag.encode_path("/home/alice/.claude") == "-home-alice--claude"


def test_encode_path_known_mnt_path():
    encoded = session_tag.encode_path("/mnt/c/Users/alice/repos/myproject")
    assert encoded == "-mnt-c-Users-alice-repos-myproject"


def test_encode_path_preserves_alphanumeric():
    assert session_tag.encode_path("/repos/myProject123") == "-repos-myProject123"


# ---------------------------------------------------------------------------
# main() — no CLD_SESSION_TAG
# ---------------------------------------------------------------------------

def test_no_tag_env_returns_zero_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("CLD_SESSION_TAG", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin("{}"))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    assert not (tmp_path / "sessions.db").exists()


# ---------------------------------------------------------------------------
# main() — happy path: session_id present in stdin JSON
# ---------------------------------------------------------------------------

def test_records_tag_in_sessions_db(tmp_path, monkeypatch):
    payload = json.dumps({"session_id": "abc-123", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    db_path = tmp_path / "sessions.db"
    assert sessions_db.lookup_tags(["abc-123"], path=db_path) == {"abc-123": "my-feature"}


def test_creates_sessions_db_if_absent(tmp_path, monkeypatch):
    payload = json.dumps({"session_id": "uuid-xyz", "cwd": "/some/new/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "cool-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    new_dir = tmp_path / "new-sessions-dir"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(new_dir))

    rc = session_tag.main()

    assert rc == 0
    assert (new_dir / "sessions.db").exists()


# ---------------------------------------------------------------------------
# main() — missing session_id
# ---------------------------------------------------------------------------

def test_missing_session_id_returns_zero_and_logs(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"cwd": "/some/path"})
    monkeypatch.setenv("CLD_SESSION_TAG", "some-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    assert not (tmp_path / "sessions.db").exists()
    err = capsys.readouterr().err
    assert "[session-tag]" in err


# ---------------------------------------------------------------------------
# main() — bad stdin JSON
# ---------------------------------------------------------------------------

def test_invalid_json_on_stdin_returns_zero_and_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLD_SESSION_TAG", "some-tag")
    monkeypatch.setattr("sys.stdin", _stdin("NOT JSON"))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "[session-tag]" in err


# ---------------------------------------------------------------------------
# main() — write failure is silent (never raises)
# ---------------------------------------------------------------------------

def test_write_failure_returns_zero_and_logs(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"session_id": "bad-write", "cwd": "/write/fail/path"})
    monkeypatch.setenv("CLD_SESSION_TAG", "fail-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))

    # Point CCST_SESSIONS_DIR at a FILE (not a dir) so mkdir fails with OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("block")
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(blocker))

    rc = session_tag.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "[session-tag]" in err


# ---------------------------------------------------------------------------
# main() — .last-opened -> sessions.db row
# ---------------------------------------------------------------------------

def test_last_opened_recorded_when_cld_session_dir_set(tmp_path, monkeypatch):
    """CLD_SESSION_DIR shaped like <project>/cc-sessions/<basename>: a row is
    upserted with a fresh last_opened timestamp."""
    project = tmp_path / "myproj"
    sess_dir = project / "cc-sessions" / "20260711-my-feature"
    sess_dir.mkdir(parents=True)
    payload = json.dumps({"session_id": "open-test", "cwd": str(project)})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    db_dir = tmp_path / "db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    rc = session_tag.main()

    assert rc == 0
    rows = sessions_db.list_sessions(path=db_dir / "sessions.db")
    assert len(rows) == 1
    assert rows[0].basename == "20260711-my-feature"
    assert rows[0].project_dir == project
    assert rows[0].last_opened > 0.0


def test_last_opened_mtime_updated_when_row_already_exists(tmp_path, monkeypatch):
    project = tmp_path / "myproj"
    sess_dir = project / "cc-sessions" / "20260711-my-feature"
    sess_dir.mkdir(parents=True)
    db_path = tmp_path / "db" / "sessions.db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_path.parent))
    sessions_db.touch_last_opened(project, "20260711-my-feature", path=db_path, when=100.0)

    payload = json.dumps({"session_id": "open-test2", "cwd": str(project)})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setattr("sys.stdin", _stdin(payload))

    session_tag.main()

    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].last_opened > 100.0


def test_last_opened_not_recorded_when_no_cld_session_dir(tmp_path, monkeypatch):
    payload = json.dumps({"session_id": "no-dir", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "no-dir-tag")
    monkeypatch.delenv("CLD_SESSION_DIR", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    assert sessions_db.list_sessions(path=tmp_path / "sessions.db") == []


def test_last_opened_not_recorded_when_dir_not_shaped_like_cc_sessions(tmp_path, monkeypatch):
    """CLD_SESSION_DIR not under a cc-sessions/ parent: no row written, no error."""
    sess_dir = tmp_path / "sess"
    sess_dir.mkdir()
    payload = json.dumps({"session_id": "shape-test", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "shape-tag")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    db_dir = tmp_path / "db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    rc = session_tag.main()

    assert rc == 0
    assert sessions_db.list_sessions(path=db_dir / "sessions.db") == []


# ---------------------------------------------------------------------------
# main() — additionalContext emission (unaffected by the storage rewrite)
# ---------------------------------------------------------------------------

def test_no_tag_emits_no_additional_context(monkeypatch, capsys):
    monkeypatch.delenv("CLD_SESSION_TAG", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin("{}"))

    session_tag.main()

    assert capsys.readouterr().out == ""


def test_new_mode_additional_context(tmp_path, monkeypatch, capsys):
    sess_dir = tmp_path / "cc-sessions" / "20260711-my-feature"
    payload = json.dumps({"session_id": "sid-new", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setenv("CLD_SESSION_MODE", "new")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))

    rc = session_tag.main()
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "ccd shell wrapper" in msg
    assert "my-feature" in msg
    assert str(sess_dir) in msg
    assert "/rename is unnecessary" in msg


def test_resume_mode_additional_context(tmp_path, monkeypatch, capsys):
    sess_dir = tmp_path / "cc-sessions" / "20260701-my-feature"
    payload = json.dumps({"session_id": "sid-resume", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setenv("CLD_SESSION_MODE", "resume")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))

    rc = session_tag.main()
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "ccr shell wrapper" in msg
    assert "being resumed today" in msg
    assert str(sess_dir) in msg


def test_defaults_to_new_mode_when_cld_session_mode_unset(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"session_id": "sid-default", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(tmp_path / "sess"))
    monkeypatch.delenv("CLD_SESSION_MODE", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))

    session_tag.main()
    out = json.loads(capsys.readouterr().out)

    assert "ccd shell wrapper" in out["hookSpecificOutput"]["additionalContext"]


def test_additional_context_emitted_even_when_session_id_missing(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(tmp_path / "sess"))
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))

    rc = session_tag.main()
    err = capsys.readouterr()

    assert rc == 0
    assert "[session-tag]" in err.err
    out = json.loads(err.out)
    assert "my-feature" in out["hookSpecificOutput"]["additionalContext"]


def test_session_dir_falls_back_to_date_tag_when_cld_session_dir_unset(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"session_id": "sid-fallback", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.delenv("CLD_SESSION_DIR", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))

    session_tag.main()
    out = json.loads(capsys.readouterr().out)

    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "cc-sessions/" in msg
    assert "-my-feature/" in msg


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _stdin:
    """Minimal stdin mock that provides .read()."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/test_session_tag.py -v`
Expected: PASS (21 tests)

- [ ] **Step 4: Commit**

```bash
git add src/cccs_hooks/session_tag.py tests/test_session_tag.py
git commit -m "feat(sessions-db): session_tag hook writes tag + last_opened to sessions.db"
```

---

## Task 6: `cccs_hooks/after_response.py` — write `last_active` to `sessions.db`

**Files:**
- Modify: `src/cccs_hooks/after_response.py`
- Modify: `tests/test_after_response.py`

- [ ] **Step 1: Rewrite the implementation**

```python
# src/cccs_hooks/after_response.py
"""Stop hook: records a session-activity sentinel into sessions.db.

Fires via Stop event (after each Claude response, not once per session).
Upserts the sessions table's last_active timestamp so `ccs --order-by active`
can sort sessions by recency of Claude activity without a filesystem walk.
Never blocks, never warns.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from cc_session_tools.lib import sessions_db


def main(argv: list[str] | None = None) -> int:
    session_dir_str = os.environ.get("CLD_SESSION_DIR", "")
    if session_dir_str:
        session_dir_path = Path(session_dir_str)
        if session_dir_path.parent.name == "cc-sessions":
            try:
                sessions_db.touch_last_active(
                    session_dir_path.parent.parent, session_dir_path.name
                )
            except (OSError, sqlite3.Error) as exc:
                print(f"[after-response] Failed to record .last-active: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Rewrite the test file**

```python
# tests/test_after_response.py
"""Tests for cccs_hooks.after_response — Stop hook that records last_active
into sessions.db."""
from __future__ import annotations

from pathlib import Path

from cccs_hooks import after_response
from cc_session_tools.lib import sessions_db


def test_last_active_recorded_when_cld_session_dir_set(tmp_path: Path, monkeypatch) -> None:
    """CLD_SESSION_DIR shaped like <project>/cc-sessions/<basename>: a row is
    upserted with a fresh last_active timestamp."""
    project = tmp_path / "myproj"
    sess_dir = project / "cc-sessions" / "20260711-my-feature"
    sess_dir.mkdir(parents=True)
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    db_dir = tmp_path / "db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    rc = after_response.main()

    assert rc == 0
    rows = sessions_db.list_sessions(path=db_dir / "sessions.db")
    assert len(rows) == 1
    assert rows[0].basename == "20260711-my-feature"
    assert rows[0].last_active > 0.0


def test_last_active_updates_existing_row_repeatedly(tmp_path: Path, monkeypatch) -> None:
    """Fires after every response — each call must bump the timestamp forward."""
    project = tmp_path / "myproj"
    sess_dir = project / "cc-sessions" / "20260711-my-feature"
    sess_dir.mkdir(parents=True)
    db_dir = tmp_path / "db"
    db_path = db_dir / "sessions.db"
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))
    sessions_db.touch_last_active(project, "20260711-my-feature", path=db_path, when=100.0)

    after_response.main()

    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].last_active > 100.0


def test_last_active_not_recorded_when_cld_session_dir_not_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLD_SESSION_DIR", raising=False)
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = after_response.main()

    assert rc == 0
    assert not (tmp_path / "sessions.db").exists()


def test_last_active_not_recorded_when_dir_not_shaped_like_cc_sessions(
    tmp_path: Path, monkeypatch
) -> None:
    sess_dir = tmp_path / "sess"
    sess_dir.mkdir()
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    db_dir = tmp_path / "db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    rc = after_response.main()

    assert rc == 0
    assert sessions_db.list_sessions(path=db_dir / "sessions.db") == []


def test_write_failure_logs_to_stderr_no_exception(tmp_path: Path, monkeypatch, capsys) -> None:
    """DB write failure (e.g. unwritable target): error printed to stderr
    (contains [after-response]), no exception propagated."""
    project = tmp_path / "myproj"
    sess_dir = project / "cc-sessions" / "20260711-my-feature"
    sess_dir.mkdir(parents=True)
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))

    # Point CCST_SESSIONS_DIR at a FILE (not a dir) so mkdir fails with OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("block")
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(blocker))

    rc = after_response.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "[after-response]" in err
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/test_after_response.py -v`
Expected: PASS (5 tests)

- [ ] **Step 4: Commit**

```bash
git add src/cccs_hooks/after_response.py tests/test_after_response.py
git commit -m "feat(sessions-db): after-response hook writes last_active to sessions.db"
```

---

## Task 7: `lib/sessions.py` — `find_jsonl_for_session` reads from `sessions.db` (D5: drop Strategy 1b)

**Files:**
- Modify: `src/cc_session_tools/lib/sessions.py`
- Modify: `tests/test_empty_session.py`

- [ ] **Step 1: Replace `find_jsonl_for_session` and remove the flat-tags-dir duplication**

In `src/cc_session_tools/lib/sessions.py`, delete lines 15-24 (`DEFAULT_SESSION_TAGS_DIR` and
`_session_tags_dir()`) entirely — this is the duplication the brief flags (a second copy of the same
constant/function already existed in `cccs_hooks/session_tag.py`, removed in Task 5). Then replace
`find_jsonl_for_session` (original lines 335-410):

```python
def find_jsonl_for_session(basename: str, project_dir: Path) -> Path | None:
    """Locate the JSONL transcript for a cc-sessions/<basename>/ directory.

    Strategy:
      1. Batch-lookup every *.jsonl in the transcript dir against sessions.db's
         session_tags table (uuid -> tag). Defence-in-depth: if a tag match has
         no custom-title record (e.g. a hook sub-process transcript that
         inherited the parent tag via env-var inheritance), treat it as
         tentative and prefer a custom-title match from Strategy 2.
      2. Fall back to scanning JSONL `custom-title` records for a match. Also
         runs when Strategy 1 found only a tentative (unconfirmed) match.

    A third, file-based fallback strategy (scanning for legacy .tag files left
    directly in the transcript dir, from a since-retired pre-sessions.db
    migration) has been removed — see design decision D5 in
    docs/superpowers/plans/2026-07-13-data-store-uplift-04-sessions-db.md.

    Returns the resolved jsonl Path, or None if no match found.
    """
    from cc_session_tools.lib import sessions_db

    transcript_dir = transcript_dir_for_project(project_dir)
    if not transcript_dir.is_dir():
        return None

    suffix = session_tag(basename)
    if suffix is None:
        return None

    jsonls = list(transcript_dir.glob("*.jsonl"))
    tag_map = sessions_db.lookup_tags([j.stem for j in jsonls])

    # Strategy 1: sessions.db tag lookup.
    tag_match: Path | None = None
    for jsonl in jsonls:
        content = tag_map.get(jsonl.stem)
        if content is None:
            continue
        if content == suffix or content == basename:
            if _jsonl_has_custom_title(jsonl, basename, suffix):
                return jsonl  # confirmed: tag row and custom-title agree
            tag_match = tag_match or jsonl  # tentative: no custom-title yet

    # Strategy 2: scan JSONLs for custom-title records (slower fallback).
    # Runs even when Strategy 1 found a tentative match: a custom-title match
    # on a different JSONL overrides the unconfirmed tag-row match.
    for jsonl in jsonls:
        try:
            with jsonl.open() as f:
                for line in f:
                    line = line.strip()
                    if not line or '"custom-title"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "custom-title":
                        title = rec.get("customTitle") or rec.get("title") or rec.get("name") or ""
                        if title == basename or title == suffix:
                            return jsonl
        except OSError:
            continue

    return tag_match
```

- [ ] **Step 2: Update `tests/test_empty_session.py`**

Replace the fixture and `_write_tag` helper (original lines 17-55) — every call site
(`_write_tag(tags_dir, uuid, tag)`, 14 occurrences) keeps working unmodified since the tuple element
name `tags_dir` and the helper's positional signature are both preserved (only their *meaning*
changes from "flat tag-file dir" to "sessions.db path"):

```python
@pytest.fixture
def synthetic_project(tmp_path, monkeypatch):
    """Synthesise a project with a cc-sessions directory, a transcript dir,
    and a sessions.db under a fake HOME so the encoded path matches what the
    helpers expect."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    # sessions.db location — controlled via env var so sessions.py picks it up.
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    project = fake_home / "repos" / "demo"
    project.mkdir(parents=True)
    (project / "cc-sessions").mkdir()

    return fake_home, project, db_dir


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _write_tag(tags_dir: Path, uuid: str, tag: str) -> None:
    """Record uuid -> tag in sessions.db (the `tags_dir` parameter name is
    kept for call-site compatibility across this file's many pre-existing
    tests; it now names the sessions.db *directory*, matching what the
    synthetic_project fixture returns)."""
    from cc_session_tools.lib import sessions_db
    sessions_db.write_tag(uuid, tag, path=tags_dir / "sessions.db")
```

No other line in the file changes — every `_write_tag(tags_dir, ...)` call site and every
`find_jsonl_for_session(basename, project)` assertion is unaffected.

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/test_empty_session.py -v`
Expected: PASS (13 tests)

- [ ] **Step 4: Commit**

```bash
git add src/cc_session_tools/lib/sessions.py tests/test_empty_session.py
git commit -m "feat(sessions-db): find_jsonl_for_session reads tags from sessions.db, drop legacy Strategy 1b"
```

---

## Task 8: `lib/sessions.py` — `find_matching_sessions` reads from `sessions.db`

**Files:**
- Modify: `src/cc_session_tools/lib/sessions.py`
- Modify: `tests/test_sessions.py`

- [ ] **Step 1: Replace `find_matching_sessions`**

Replace the original implementation (lines 69-85):

```python
def find_matching_sessions(fragment: str, roots: list[Path]) -> list[SessionMatch]:
    """Substring-match `fragment` against every session basename recorded in
    sessions.db, scoped to projects whose direct parent is one of `roots`
    (mirrors the historical filesystem-walk scoping: only projects directly
    under a configured root are searched)."""
    from cc_session_tools.lib import sessions_db

    out: list[SessionMatch] = []
    for row in sessions_db.list_sessions():
        if fragment not in row.basename:
            continue
        if row.project_dir.parent not in roots:
            continue
        session_dir = row.project_dir / "cc-sessions" / row.basename
        out.append(SessionMatch(
            basename=row.basename,
            project_dir=row.project_dir,
            session_dir=session_dir,
        ))
    return out
```

`iter_sessions()` (original lines 61-66) is **kept unchanged** — it remains a pure filesystem utility,
still directly unit-tested, and is reused by the migration script's backfill walk in Task 13.

- [ ] **Step 2: Update `tests/test_sessions.py`**

`test_find_matching_sessions_substring_match` and `test_find_matching_sessions_returns_empty_for_no_match`
(original lines 57-76) create sessions purely via `mkdir` and expect `find_matching_sessions` to find
them by walking the filesystem. Since matching is now DB-backed, these tests must also write matching
`sessions.db` rows. Replace both:

```python
def test_find_matching_sessions_substring_match(tmp_path, monkeypatch):
    from cc_session_tools.lib import sessions_db

    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    root = tmp_path / "myroot"
    proj = root / "myproject"
    cc = proj / "cc-sessions"
    (cc / "20260504-foo-bar").mkdir(parents=True)
    (cc / "20260503-baz").mkdir()
    sessions_db.ensure_session_row(proj, "20260504-foo-bar")
    sessions_db.ensure_session_row(proj, "20260503-baz")

    matches = sessions.find_matching_sessions("foo", roots=[root])
    assert len(matches) == 1
    assert matches[0].basename == "20260504-foo-bar"
    assert matches[0].project_dir == proj


def test_find_matching_sessions_returns_empty_for_no_match(tmp_path, monkeypatch):
    from cc_session_tools.lib import sessions_db

    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    root = tmp_path / "myroot"
    proj = root / "myproject"
    cc = proj / "cc-sessions"
    (cc / "20260504-foo").mkdir(parents=True)
    sessions_db.ensure_session_row(proj, "20260504-foo")

    assert sessions.find_matching_sessions("nope", roots=[root]) == []


def test_find_matching_sessions_excludes_projects_outside_roots(tmp_path, monkeypatch):
    """A row whose project_dir is not directly under any given root must not match."""
    from cc_session_tools.lib import sessions_db

    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    other_root = tmp_path / "other-root"
    proj = other_root / "myproject"
    sessions_db.ensure_session_row(proj, "20260504-foo")

    configured_root = tmp_path / "configured-root"
    assert sessions.find_matching_sessions("foo", roots=[configured_root]) == []
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions.py -v`
Expected: PASS (23 tests)

- [ ] **Step 4: Commit**

```bash
git add src/cc_session_tools/lib/sessions.py tests/test_sessions.py
git commit -m "feat(sessions-db): find_matching_sessions queries sessions.db instead of walking cc-sessions/"
```

---

## Task 9: `cli/ccd.py` — insert a `sessions.db` row when a session directory is created

**Files:**
- Modify: `src/cc_session_tools/cli/ccd.py`
- Modify: `tests/test_cli_ccd.py`

- [ ] **Step 1: Insert the row right after directory creation**

In `src/cc_session_tools/cli/ccd.py`, immediately after (original lines 190-191):

```python
    (session_dir / "working").mkdir(parents=True, exist_ok=True)
    (session_dir / "out").mkdir(parents=True, exist_ok=True)
```

add:

```python
    from cc_session_tools.lib import sessions_db
    sessions_db.ensure_session_row(real_pwd, session_name)
```

This is a direct, unguarded call (no try/except) — per D6/coding-standards, if `sessions.db` is
genuinely unwritable that is a real problem `ccd` (an interactive foreground CLI, not a
never-crash hook) should surface, not silently swallow. `ensure_session_row` is a safety net in case
the SessionStart hook never fires; the hook's own `touch_last_opened` upsert (Task 5) is the normal
path and will run moments later regardless once `claude` actually starts.

- [ ] **Step 2: Update `tests/test_cli_ccd.py`**

The `fake_home` fixture (original lines 29-37) sets `CCCS_SESSION_TAGS_DIR` and `_write_transcript`
(lines 40-62) writes `.tag` files via the now-removed `_session_tags_dir()`. Update both:

```python
from cc_session_tools.cli import ccd
from cc_session_tools.lib import sessions_db
from cc_session_tools.lib.sessions import session_tag, transcript_dir_for_project


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    # Redirect sessions.db so transcript lookup is hermetic and never reads
    # the developer's real ~/.local/share/claude/sessions.db.
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    return home


def _write_transcript(proj: Path, basename: str, *, user_typed: bool) -> Path:
    """Fabricate a JSONL transcript for `basename` under proj's transcript dir.

    user_typed=True  -> contains a real typed message (is_empty_session -> False).
    user_typed=False -> contains only a SessionStart hook record (still "empty").
    """
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True, exist_ok=True)
    stem = f"uuid-{basename}"
    sessions_db.write_tag(stem, session_tag(basename) or basename)
    if user_typed:
        rec = {"type": "user", "message": {"content": "do the thing"}}
    else:
        rec = {
            "type": "user",
            "isMeta": True,
            "message": {"content": "<command-name>SessionStart</command-name>"},
        }
    jsonl = t_dir / f"{stem}.jsonl"
    jsonl.write_text(json.dumps(rec) + "\n")
    return jsonl
```

(The `_session_tags_dir` import is dropped from the top-level import block; the rest of the file's
imports and test bodies are unaffected.)

- [ ] **Step 3: Add a test for the new DB-insert behaviour**

Append:

```python
def test_ccd_inserts_sessions_db_row(fake_home, tmp_path, monkeypatch, captured_launch):
    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)
    monkeypatch.chdir(proj)

    rc = ccd.main(["ccd-db-test"])

    assert rc is None or "cmd" in captured_launch  # ccd execs; dry_run not used here
    today = datetime.now().strftime("%Y%m%d")
    rows = sessions_db.list_sessions(path=tmp_path / "db" / "sessions.db")
    assert any(r.basename == f"{today}-ccd-db-test" and r.project_dir == proj for r in rows)
```

Adjust the assertion for whatever this file's existing convention is for asserting `ccd.main()`
completed and `launch_claude` was captured (match the style of the neighbouring
`test_ccd_creates_session_dir_and_launches_claude` test already in this file, reusing its exact
`captured_launch`/`fake_home`/`_set_repo_root` fixtures).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_ccd.py -v`
Expected: PASS (all existing tests + the new one)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccd.py tests/test_cli_ccd.py
git commit -m "feat(sessions-db): ccd inserts a sessions.db row when creating a session dir"
```

---

## Task 10: `cli/ccs.py` — DB-backed session enumeration (items 1-3 of the perf brief; D1 for `--order-by update`)

This is the task that actually fixes `ccl --global`'s and `ccr`'s performance problem.

**Files:**
- Modify: `src/cc_session_tools/cli/ccs.py`
- Modify: `tests/test_ccs_sentinel_sort.py`

- [ ] **Step 1: Remove `_collect_pairs` and `_get_sentinel_mtime`; add `_collect_session_rows`**

In `src/cc_session_tools/cli/ccs.py`:

1. Change the import block (original lines 17-25) — drop `iter_sessions` (no longer used here):

```python
from cc_session_tools import __version__
from cc_session_tools.lib.roots import RootsConfigError, load_session_roots
from cc_session_tools.lib.sessions import (
    grep_files,
    session_is_empty_safe,
    session_start_date,
    transcript_dir_for_project,
)
```

2. Delete `_get_sentinel_mtime()` (original lines 122-127) entirely — every caller is replaced in
   this task by direct `SessionRow.last_opened`/`.last_active` field reads (no filesystem stat).

3. Delete `_collect_pairs()` (original lines 386-407) and replace it with:

```python
def _collect_session_rows(do_global: bool) -> list["sessions_db.SessionRow"]:
    """Return sessions.db rows for the search scope. Replaces the old
    O(roots x projects x sessions) filesystem walk (_collect_pairs +
    iter_sessions) with one indexed query."""
    from cc_session_tools.lib import sessions_db

    if do_global:
        try:
            roots = load_session_roots()
        except RootsConfigError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        return [r for r in sessions_db.list_sessions() if r.project_dir.parent in roots]

    cwd = Path.cwd().resolve()
    return sessions_db.list_sessions(project_dir=cwd)
```

(`sessions_db` needs a module-level import too — add `from cc_session_tools.lib import sessions_db`
near the top import block alongside the others, rather than importing it lazily inside the function,
since it's now used in multiple places in this file per the next steps.)

- [ ] **Step 2: Rewrite the sessions-building block in `main()`**

Replace (original lines 1049-1064):

```python
    debug(f"scope: {'global' if effective_global else f'cwd={Path.cwd()}'}")
    pairs = _collect_pairs(effective_global)
    if not pairs:
        if effective_global:
            print("ccs: no sessions found in any configured root", file=sys.stderr)
        else:
            print("ccs: no cc-sessions/ in current directory", file=sys.stderr)
        return 1

    sessions: list[tuple[Path, Path]] = []
    for cc, proj in pairs:
        for sess in iter_sessions(cc):
            if session_start_date(sess.name) is None:
                continue
            sessions.append((sess, proj))
    debug(f"sessions found: {len(sessions)}")
```

with:

```python
    debug(f"scope: {'global' if effective_global else f'cwd={Path.cwd()}'}")
    session_rows = _collect_session_rows(effective_global)
    if not session_rows:
        if effective_global:
            print("ccs: no sessions found in any configured root", file=sys.stderr)
        else:
            print("ccs: no cc-sessions/ in current directory", file=sys.stderr)
        return 1
    debug(f"sessions found: {len(session_rows)}")

    # (session_dir, project_dir) pairs — kept for every downstream code path in
    # this file that still needs real filesystem access (emptiness/contents/
    # messages search, --order-by update's rglob walk; see design decision D1).
    sessions: list[tuple[Path, Path]] = [
        (row.project_dir / "cc-sessions" / row.basename, row.project_dir)
        for row in session_rows
    ]
    # basename -> SessionRow, for O(1) opened/active lookups with zero
    # filesystem stat calls (replaces the old per-session _get_sentinel_mtime).
    row_by_basename = {row.basename: row for row in session_rows}
```

Everything below this point in `main()` — the hooks/date/emptiness filters, the footer print, the
list-mode `update`-branch, the search-mode dispatch — is unchanged **except** the two `opened`/
`active` branches, updated next. `session_start_date` remains imported and used elsewhere in the file
(the `--order-by start` list-mode sort key still calls it), so it stays in the import list.

- [ ] **Step 3: Rewrite the list-mode `opened`/`active` branch**

Replace (original lines 1147-1163):

```python
        elif order_by in ("opened", "active"):
            label = order_by  # "opened" or "active"

            def _row_mtime(pair: tuple[Path, Path]) -> float:
                s, _proj = pair
                row = row_by_basename.get(s.name)
                if row is None:
                    return 0.0
                return row.last_opened if order_by == "opened" else row.last_active

            sessions_sorted_sentinel = sorted(sessions, key=_row_mtime, reverse=True)
            for s, proj in sessions_sorted_sentinel:
                mtime = _row_mtime((s, proj))
                display_name = _maybe_link(s.name, s)
                dt_str = _format_sentinel_dt(mtime, label)
                if effective_global:
                    print(f"{display_name} ({dt_str}, {_display_path(proj)})")
                else:
                    print(f"{display_name} ({dt_str})")
```

- [ ] **Step 4: Rewrite the search-mode opened/active population step**

Replace (original lines 1229-1244, the `elif order_by == "opened": ... elif order_by == "active": ...`
block that built a `sentinel_cache` dict via `_get_sentinel_mtime`):

```python
    elif order_by == "opened":
        for r in all_results:
            row = row_by_basename.get(r.basename)
            r.opened_mtime = row.last_opened if row is not None else 0.0
    elif order_by == "active":
        for r in all_results:
            row = row_by_basename.get(r.basename)
            r.active_mtime = row.last_active if row is not None else 0.0
```

The `update`-branch immediately above this (`_get_session_update_mtime` loop) is **unchanged** — see
D1.

- [ ] **Step 5: Update `tests/test_ccs_sentinel_sort.py`**

Remove the entire `TestGetSentinelMtime` class (original lines 47-64) — `_get_sentinel_mtime` no
longer exists. Update the `_get_sentinel_mtime` import at the top of the file:

```python
from cc_session_tools.cli import ccs
from cc_session_tools.cli.ccs import _Result, _sort_results
```

Update the `fake_repos`/`_make_session` fixtures so every session dir created also gets a matching
`sessions.db` row, and the `.last-opened`/`.last-active` files that tests touch directly are replaced
by `sessions_db.touch_last_opened`/`touch_last_active` calls:

```python
@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    return home


@pytest.fixture
def fake_repos(fake_home, tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    repos.mkdir()
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos))
    return repos


def _make_session(repos: Path, project: str, basename: str) -> Path:
    from cc_session_tools.lib import sessions_db
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    sessions_db.ensure_session_row(repos / project, basename)
    return sess
```

Then, in every test that currently does `(sess / ".last-opened").touch()` or
`(sess / ".last-active").touch()` (the `TestListModeSentinelOutput`/`TestSearchModeSentinelOutput`
classes), replace with the corresponding `sessions_db.touch_last_opened(repos / project, basename)` /
`sessions_db.touch_last_active(...)` call. For example (original
`test_list_mode_opened_includes_label_and_timestamp`):

```python
    def test_list_mode_opened_includes_label_and_timestamp(self, fake_repos, monkeypatch, capsys):
        from cc_session_tools.lib import sessions_db
        proj = fake_repos / "myproj"
        sess = _make_session(fake_repos, "myproj", "20260612-a-sess")
        sessions_db.touch_last_opened(proj, "20260612-a-sess")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "opened:" in out
        assert "20260612-a-sess" in out
```

Apply the same mechanical substitution (`(sess / ".last-opened").touch()` →
`sessions_db.touch_last_opened(proj, basename)`, `(sess / ".last-active").touch()` →
`sessions_db.touch_last_active(proj, basename)`) to every remaining test in
`TestListModeSentinelOutput` and `TestSearchModeSentinelOutput`. The `TestSortResultsSentinel` class
(pure `_sort_results`/`_Result` unit tests, no filesystem/DB interaction) is unaffected.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccs_sentinel_sort.py -v`
Expected: PASS (all tests except the removed `TestGetSentinelMtime` class — count decreases by 3)

- [ ] **Step 7: Add a session-count-scaling regression test proving the performance requirement**

This is the explicit, testable acceptance criterion for the design spec's 2026-07-13 performance
requirement ("`ccl`, `ccr`, and `ccs` must all become measurably faster at listing and matching
sessions by title/tag" — see `data-stores-design-spec.md` §7.2). A benchmark comparing old-vs-new
code isn't possible post-migration (the old `_collect_pairs`/`iter_sessions` walk is deleted by
this same task) — instead, assert the new query-based path has **flat cost as session count
grows**, which is the practical, durable way to catch a future regression back to an O(n)
filesystem walk.

**Design note (revised after adversarial review):** an earlier draft of this test used wall-clock
timing bounds (assert N=2000 runs in under `small_elapsed * 5 + 0.5` seconds). A review empirically
proved this doesn't work: a naive `iterdir()` + `stat()` per-session walk over 50 → 2050 synthetic
entries took only 0.48ms → 15.4ms locally (tmpfs is fast) — a genuine 32x-slower O(n) regression
would sail through a timing bound that generous undetected. **Timing is not a reliable signal at
this scale on fast filesystems/CI runners.** The durable fix is to assert the *mechanism*, not the
*wall-clock cost*: count actual filesystem `stat()` calls and assert the count does not grow with
the session count, since the whole point of the migration is that title/tag/timestamp data now
comes from indexed DB columns with zero per-session filesystem access, not that the same
stat-per-session pattern merely got faster.

Add to `tests/test_ccs_sentinel_sort.py`:

```python
from unittest.mock import patch


class TestSessionEnumerationScaling:
    """Regression test for the 2026-07-13 design-spec performance requirement: ccl/ccr/ccs
    session-title/tag lookup must be an indexed sessions.db query making zero per-session
    filesystem stat() calls, not an O(n) directory walk that merely happens to run fast on a
    given machine. Scoped to titles/tags/metadata only — NOT session content search
    (--order-by update, which is unchanged by design and DOES still stat files - see D1)."""

    def _seed_sessions(self, fake_repos, count: int, start_at: int = 0) -> Path:
        from cc_session_tools.lib import sessions_db

        proj = fake_repos / "scaleproj"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "cc-sessions").mkdir(exist_ok=True)
        for i in range(start_at, start_at + count):
            name = f"20260101-session-{i:05d}"
            (proj / "cc-sessions" / name).mkdir()
            sessions_db.touch_last_opened(proj, name)
        return proj

    def test_order_by_active_makes_no_per_session_stat_calls_regardless_of_count(
        self, fake_home, fake_repos, monkeypatch
    ):
        """The opened/active list-mode branch (Task 10 Step 3) reads mtimes from
        row_by_basename (an in-memory dict built from one sessions.db query), not from
        Path.stat() on each session's sentinel file — assert that directly by counting real
        Path.stat() invocations, which must stay flat as N grows from 50 to 2000."""
        proj = self._seed_sessions(fake_repos, 50)
        monkeypatch.chdir(proj)

        stat_calls = []
        real_stat = Path.stat

        def _counting_stat(self, *a, **kw):
            stat_calls.append(self)
            return real_stat(self, *a, **kw)

        with patch.object(Path, "stat", _counting_stat):
            rc_small = ccs.main(["--order-by", "active"])
            small_count = len(stat_calls)
        assert rc_small == 0

        self._seed_sessions(fake_repos, 1950, start_at=50)  # -> 2000 total
        stat_calls.clear()
        with patch.object(Path, "stat", _counting_stat):
            rc_large = ccs.main(["--order-by", "active"])
            large_count = len(stat_calls)
        assert rc_large == 0

        # A 40x growth in session count (50 -> 2000) must not grow the stat() call count at
        # all for the mtime-lookup path itself — any per-session stat call here is a direct
        # regression to the pre-migration _get_sentinel_mtime walk. Allow a small constant
        # slack (<=5) for incidental stats unrelated to sentinel lookup (e.g. cwd resolution),
        # never a count that scales with N.
        assert large_count <= small_count + 5, (
            f"stat() call count grew with session count ({small_count} -> {large_count} for "
            f"50 -> 2000 sessions) — this is the exact O(n) filesystem-walk regression this "
            f"test exists to catch."
        )

    def test_global_enumeration_under_absolute_time_bound_at_2000_sessions(
        self, fake_home, fake_repos, monkeypatch
    ):
        """Secondary sanity check, not the primary regression guard (see design note above) —
        a single indexed query + formatting 2000 rows should still complete quickly in absolute
        terms. Kept as a coarse smoke test; the stat-call-count test above is what actually
        proves the mechanism."""
        import time

        self._seed_sessions(fake_repos, 2000)
        start = time.perf_counter()
        rc = ccs.main(["--global"])
        elapsed = time.perf_counter() - start
        assert rc == 0
        assert elapsed < 1.0
```

- [ ] **Step 8: Run the new benchmark tests to verify they pass**

Run: `uv run pytest tests/test_ccs_sentinel_sort.py::TestSessionEnumerationScaling -v`
Expected: PASS. If `test_order_by_active_makes_no_per_session_stat_calls_regardless_of_count`
fails because `large_count` scales with N, that is a real regression to fix in Task 10 Steps 2-3
(some code path is still calling `.stat()`/touching the filesystem per session instead of reading
from `row_by_basename`) — do not raise the slack constant to make the test pass without first
confirming the growth isn't a genuine reintroduced per-session filesystem walk.

- [ ] **Step 9: Commit**

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_ccs_sentinel_sort.py
git commit -m "perf(ccs): enumerate + sort sessions from sessions.db instead of filesystem walks

Adds a scaling regression test enforcing the design-spec's 2026-07-13 performance
requirement: ccl/ccr/ccs session title/tag lookup is a flat-cost indexed query,
not an O(n) directory walk."
```

- [ ] **Step 10: Add `--limit`/`-n` — "most recent N sessions" as a genuinely indexed query**

Added per an explicit 2026-07-13 requirement: `ccl`/`ccs` must efficiently support "give me the
most recent N sessions", not just "give me all sessions sorted, which happens to let a human read
only the top few." Without this, `sessions_db.list_sessions()`'s new `order_by`/`limit` params
(Task 2) go unused by the CLI, and "most recent N" would still mean fetching every matching row
and slicing in Python — no better than before for that specific use case.

Find the `--order-by` argparse definition in `_build_parser()` (or equivalent parser-construction
function) in `src/cc_session_tools/cli/ccs.py` and add a sibling argument immediately after it:

```python
    parser.add_argument(
        "-n", "--limit", type=int, default=None, metavar="N",
        help="show only the N most recent sessions (requires --order-by opened or active; "
             "pushed down as an indexed SQL LIMIT, not a post-fetch slice)",
    )
```

Validate the `--order-by`/`--limit` combination right after argument parsing (`main()`, near
where `order_by` is first read out of `args`):

```python
    if args.limit is not None and order_by not in ("opened", "active"):
        print(
            "ccs: --limit requires --order-by opened or --order-by active "
            "(start/update ordering is not database-indexed — see design decision D1)",
            file=sys.stderr,
        )
        return 2
```

Thread `limit` through `_collect_session_rows` (Task 10 Step 1) so the `LIMIT` clause is pushed
into the SQL query itself rather than applied after fetching every row:

```python
def _collect_session_rows(
    do_global: bool, *, order_by: str | None = None, limit: int | None = None
) -> list["sessions_db.SessionRow"]:
    """Return sessions.db rows for the search scope. order_by/limit push an indexed
    ORDER BY ... LIMIT into SQL (see sessions_db.list_sessions) when order_by is a
    DB-backed column - this is what makes "most recent N" an O(log n) lookup, not a
    fetch-everything-then-slice."""
    from cc_session_tools.lib import sessions_db

    if do_global:
        try:
            roots = load_session_roots()
        except RootsConfigError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        rows = sessions_db.list_sessions(order_by=order_by, limit=limit if order_by else None)
        return [r for r in rows if r.project_dir.parent in roots]

    cwd = Path.cwd().resolve()
    return sessions_db.list_sessions(
        project_dir=cwd, order_by=order_by, limit=limit if order_by else None
    )
```

Note the global-scope path still filters by `roots` *after* the DB query — if `--limit` is combined
with `--global` and a multi-root filter, the returned N rows are the N most recent **before** the
root filter is applied, which could under-return fewer than N results after filtering. This is an
accepted, documented limitation (pushing the root filter into SQL too is a reasonable future
enhancement but not required to deliver "most recent N" as an indexed query for the common case —
note this explicitly rather than silently, per this migration's "no silent gaps" convention) —
add a one-line docstring caveat as shown above ("N most recent *before* root filtering").

Update the call site in `main()` to pass `order_by`/`limit`, and skip the existing Python-side
`sorted(..., reverse=True)` step in the opened/active branch (Task 10 Step 3) when rows already
arrived pre-sorted and pre-limited from SQL:

```python
    session_rows = _collect_session_rows(
        effective_global,
        order_by=order_by if order_by in ("opened", "active") else None,
        limit=args.limit,
    )
```

In the opened/active list-mode branch (Task 10 Step 3), `session_rows` is now already in the
correct order when `args.limit` was set — drop the `sorted(sessions, key=_row_mtime,
reverse=True)` call in that case (or leave it as a no-op re-sort of an already-sorted, already
short list; either is correct, but skipping it avoids doing a second pointless sort of what's
already a sorted N-row result — do skip it, this is the whole efficiency point of Step 10).

- [ ] **Step 11: Add tests for `--limit`**

Add to `tests/test_ccs_sentinel_sort.py`:

```python
class TestLimitFlag:
    def test_limit_returns_only_n_most_recent_by_active(self, fake_home, fake_repos, monkeypatch):
        from cc_session_tools.lib import sessions_db
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True)
        (proj / "cc-sessions").mkdir()
        for i in range(10):
            name = f"20260101-sess-{i:02d}"
            (proj / "cc-sessions" / name).mkdir()
            sessions_db.touch_last_active(proj, name, when=float(i))
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "active", "--limit", "3"])
        assert rc == 0
        out = capsys.readouterr().out if False else None  # use this file's existing capsys fixture pattern

    def test_limit_without_compatible_order_by_errors(self, fake_home, fake_repos, monkeypatch, capsys):
        rc = ccs.main(["--order-by", "start", "--limit", "3"])
        assert rc == 2
        assert "requires --order-by opened or --order-by active" in capsys.readouterr().err
```

(Adjust `test_limit_returns_only_n_most_recent_by_active` to this file's actual `capsys` fixture
usage pattern — every other test in the file takes `capsys` as a direct pytest fixture parameter,
not a conditional local; the placeholder above exists only to flag that the assertion body needs
completing with a real `capsys.readouterr().out` check for exactly 3 session names, newest-first
(`sess-09`, `sess-08`, `sess-07`), matching the style of the file's other list-mode output tests.)

- [ ] **Step 12: Run tests and commit**

Run: `uv run pytest tests/test_ccs_sentinel_sort.py::TestLimitFlag -v`
Expected: PASS.

```bash
git add src/cc_session_tools/cli/ccs.py tests/test_ccs_sentinel_sort.py
git commit -m "feat(ccs): add --limit/-n for an indexed 'most recent N sessions' query

Pushes ORDER BY ... LIMIT into sessions.db directly when --order-by is opened/active,
rather than fetching every matching session and slicing in Python."
```

---

## Task 11: Mechanical fixture updates — remaining `ccs` consumer test files

Five more test files build sessions purely via `mkdir` (no DB row) and need the same one-line
addition to their local `_make_session()` helper, plus `CCCS_SESSION_TAGS_DIR` → `CCST_SESSIONS_DIR`
in their `fake_home` fixture where present. `test_ccs_session_counts.py` and `test_ccs_emptiness.py`
additionally have inline JSONL-fabrication helpers that write `.tag` files directly via
`_session_tags_dir()` — those switch to `sessions_db.write_tag()`.

**Files:**
- Modify: `tests/test_ccs_session_counts.py`
- Modify: `tests/test_ccs_emptiness.py`
- Modify: `tests/test_ccs_scope_flags.py`
- Modify: `tests/test_ccs_list_mode.py`
- Modify: `tests/test_cli_ccs.py`

- [ ] **Step 1: `tests/test_ccs_session_counts.py`**

Replace the `fake_home` fixture:

```python
@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    return home
```

Replace `_make_session`:

```python
def _make_session(repos: Path, project: str, basename: str, *, contents: str | None = None) -> Path:
    from cc_session_tools.lib import sessions_db
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    if contents is not None:
        (sess / "working" / "WORKLOG.md").write_text(contents)
    sessions_db.ensure_session_row(repos / project, basename)
    return sess
```

In both `_write_jsonl_with_user_message` and `_write_jsonl_empty` (or whatever the two inline
JSONL-fabrication helpers in this file are named), replace:

```python
    from cc_session_tools.lib.sessions import session_tag, _session_tags_dir
    tag = session_tag(basename)
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True, exist_ok=True)
    stem = f"xuser-{basename}"
    # Write tag file to the flat tags dir (respects CCCS_SESSION_TAGS_DIR).
    tags_dir = _session_tags_dir()
    tags_dir.mkdir(parents=True, exist_ok=True)
    (tags_dir / f"{stem}.tag").write_text(tag or basename)
```

with:

```python
    from cc_session_tools.lib import sessions_db
    from cc_session_tools.lib.sessions import session_tag
    tag = session_tag(basename)
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True, exist_ok=True)
    stem = f"xuser-{basename}"
    sessions_db.write_tag(stem, tag or basename)
```

(keep each helper's distinct `stem` value — `"xuser-{basename}"` / `"xempty-{basename}"` — unchanged;
only the tag-recording lines change). Run: `uv run pytest tests/test_ccs_session_counts.py -v`
Expected: PASS.

- [ ] **Step 2: `tests/test_ccs_emptiness.py`**

Apply the identical substitution as Step 1 (same `fake_home` shape, same `_make_session` shape, same
two inline JSONL helpers with `stem = "abc-user-msg"` / `stem = "abc-empty"`). Run:
`uv run pytest tests/test_ccs_emptiness.py -v`
Expected: PASS.

- [ ] **Step 3: `tests/test_ccs_scope_flags.py`**

No `CCCS_SESSION_TAGS_DIR` usage in this file's fixtures (confirmed by grep) — only `_make_session`
needs updating:

```python
def _make_session(
    repos: Path, project: str, basename: str, *, contents: str | None = None
) -> Path:
    from cc_session_tools.lib import sessions_db
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    if contents is not None:
        (sess / "working" / "WORKLOG.md").write_text(contents)
    sessions_db.ensure_session_row(repos / project, basename)
    return sess
```

Also add `monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))` to this file's `fake_home`
fixture (it currently only sets `HOME`). Run: `uv run pytest tests/test_ccs_scope_flags.py -v`
Expected: PASS.

- [ ] **Step 4: `tests/test_ccs_list_mode.py`**

Same pattern as Step 3 — update `fake_home` to add `CCST_SESSIONS_DIR`, update `_make_session`
identically. Run: `uv run pytest tests/test_ccs_list_mode.py -v`
Expected: PASS.

- [ ] **Step 5: `tests/test_cli_ccs.py`**

Same pattern as Step 3 — update `fake_home` to add `CCST_SESSIONS_DIR`, update `_make_session`
identically. Run: `uv run pytest tests/test_cli_ccs.py -v`
Expected: PASS.

- [ ] **Step 6: Run all five files together**

```bash
uv run pytest tests/test_ccs_session_counts.py tests/test_ccs_emptiness.py \
  tests/test_ccs_scope_flags.py tests/test_ccs_list_mode.py tests/test_cli_ccs.py -v
```

Expected: PASS (all tests across all five files)

- [ ] **Step 7: Commit**

```bash
git add tests/test_ccs_session_counts.py tests/test_ccs_emptiness.py \
  tests/test_ccs_scope_flags.py tests/test_ccs_list_mode.py tests/test_cli_ccs.py
git commit -m "test(ccs): update fixtures to write sessions.db rows instead of flat files"
```

---

## Task 12: `cli/ccr.py` — DB-backed matching + D6's stale-row guard on the final match

**Files:**
- Modify: `src/cc_session_tools/cli/ccr.py`
- Modify: `tests/test_ccr_orphans.py`
- Modify: `tests/test_cli_ccr.py`

- [ ] **Step 1: Rewrite the exact-match fast path**

Replace (original lines 53-74):

```python
    # Exact-match fast-path: if fragment looks like a full basename, try a
    # direct sessions.db lookup before falling back to substring search. This
    # prevents "20260504-foo" from being treated as ambiguous when
    # "20260504-foo-bar" also exists.
    exact_match: SessionMatch | None = None
    if SESSION_FULL_RE.fullmatch(args.fragment):
        from cc_session_tools.lib import sessions_db
        for row in sessions_db.find_exact(args.fragment):
            if row.project_dir.parent in roots:
                exact_match = SessionMatch(
                    basename=row.basename,
                    project_dir=row.project_dir,
                    session_dir=row.project_dir / "cc-sessions" / row.basename,
                )
                break
```

- [ ] **Step 2: Add the D6 stale-row guard right before launching**

Immediately after the existing orphan-warning block (original lines 117-122):

```python
    if m.is_orphan:
        print(
            f"ccr: warning: no on-disk session directory for '{m.basename}' "
            f"(orphan transcript only)",
            file=sys.stderr,
        )
```

add:

```python
    elif not m.session_dir.is_dir():
        print(
            f"ccr: session directory for '{m.basename}' no longer exists on disk "
            "(stale sessions.db entry). Run 'ccst sessions migrate' to resync, or "
            "pass --include-orphans if a transcript may still be resumable.",
            file=sys.stderr,
        )
        return 1
```

This single `is_dir()` call replaces what would otherwise be an O(n) check across every candidate —
per D6 it runs exactly once, on the one match `ccr` is actually about to launch into.

- [ ] **Step 3: Update `tests/test_ccr_orphans.py`**

`_make_session` (original lines 26-30) creates on-disk session dirs consumed by
`find_matching_sessions` (now DB-backed per Task 8) — add the matching row:

```python
def _make_session(repos: Path, project: str, basename: str) -> Path:
    from cc_session_tools.lib import sessions_db
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    (sess / "out").mkdir()
    sessions_db.ensure_session_row(repos / project, basename)
    return sess
```

Add `monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))` to the `fake_home` fixture
(original lines 68-72).

Add a regression test for the D6 stale-row guard:

```python
def test_stale_sessions_db_row_reports_error_not_crash(fake_repos, claude_projects, captured_launch, capsys):
    """A sessions.db row whose on-disk directory was deleted must produce a
    clean error, not a broken exec into a nonexistent directory (D6)."""
    from cc_session_tools.lib import sessions_db
    import shutil

    proj = fake_repos / "myproj"
    sess = _make_session(fake_repos, "myproj", "20260504-deleted-after")
    shutil.rmtree(sess)  # directory gone; sessions.db row still present

    rc = ccr.main(["deleted-after"])

    assert rc == 1
    assert "cmd" not in captured_launch
    err = capsys.readouterr().err
    assert "stale" in err.lower()
```

- [ ] **Step 4: Update `tests/test_cli_ccr.py`**

`_make_session` (original lines 49-53) — same pattern as Step 3:

```python
def _make_session(repos: Path, project: str, basename: str) -> Path:
    from cc_session_tools.lib import sessions_db
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    sessions_db.ensure_session_row(repos / project, basename)
    return sess
```

Add `monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))` to this file's `fake_home`
fixture (original lines 34-38).

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_ccr_orphans.py tests/test_cli_ccr.py -v
```

Expected: PASS (all existing tests + the new stale-row regression test)

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/cli/ccr.py tests/test_ccr_orphans.py tests/test_cli_ccr.py
git commit -m "perf(ccr): match sessions via sessions.db; guard the final launch against stale rows (D6)"
```

---

## Task 13: `cli/migrate_sessions_db.py` — one-shot migration script

Implements the "Migration script requirements" from the brief and the non-destructive safety rule
from `overview.md` §4: write+verify before deletion, tar-backup, never auto-delete.

**Files:**
- Create: `src/cc_session_tools/cli/migrate_sessions_db.py`
- Create: `tests/test_migrate_sessions_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_migrate_sessions_db.py
"""Tests for cc_session_tools.cli.migrate_sessions_db — one-shot migration of
the flat tag cache, activity sentinels, and cc-doctor-mutes.json into
sessions.db."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from cc_session_tools.cli.migrate_sessions_db import run_migration
from cc_session_tools.lib import doctor_mutes, sessions_db


@pytest.fixture
def layout(tmp_path):
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    mutes_file = tmp_path / "cc-doctor-mutes.json"
    root = tmp_path / "repos"
    root.mkdir()
    db_path = tmp_path / "sessions.db"
    backup_dir = tmp_path / "backups"
    return {
        "tags_dir": tags_dir,
        "mutes_file": mutes_file,
        "root": root,
        "db_path": db_path,
        "backup_dir": backup_dir,
    }


def _make_session_dir(root: Path, project: str, basename: str) -> Path:
    sess = root / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    return sess


def test_dry_run_writes_nothing(layout):
    (layout["tags_dir"] / "uuid-1.tag").write_text("my-feature\n")
    rc = run_migration(
        dry_run=True, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    assert not layout["db_path"].exists()
    assert not layout["backup_dir"].exists()


def test_migrates_tags(layout):
    (layout["tags_dir"] / "uuid-1.tag").write_text("my-feature\n")
    (layout["tags_dir"] / "uuid-2.tag").write_text("other-feature\n")
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    result = sessions_db.lookup_tags(["uuid-1", "uuid-2"], path=layout["db_path"])
    assert result == {"uuid-1": "my-feature", "uuid-2": "other-feature"}


def test_migrates_activity_sentinels(layout):
    sess = _make_session_dir(layout["root"], "myproj", "20260713-my-feature")
    (sess / ".last-opened").touch()
    (sess / ".last-active").touch()
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    rows = sessions_db.list_sessions(path=layout["db_path"])
    assert len(rows) == 1
    assert rows[0].basename == "20260713-my-feature"
    assert rows[0].last_opened > 0.0
    assert rows[0].last_active > 0.0


def test_migrates_session_with_no_sentinels(layout):
    """A session dir with no .last-opened/.last-active still gets a row (start_date only)."""
    _make_session_dir(layout["root"], "myproj", "20260713-no-sentinels")
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    rows = sessions_db.list_sessions(path=layout["db_path"])
    assert rows[0].last_opened == 0.0
    assert rows[0].last_active == 0.0


def test_migrates_doctor_mutes(layout):
    layout["mutes_file"].write_text(json.dumps({"version:pypi": "2026-07-01"}))
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    assert doctor_mutes.load_mutes(layout["db_path"]) == {"version:pypi": "2026-07-01"}


def test_writes_tar_backup_of_old_sources(layout):
    (layout["tags_dir"] / "uuid-1.tag").write_text("my-feature\n")
    layout["mutes_file"].write_text(json.dumps({"a": "2026-07-01"}))
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    backups = list(layout["backup_dir"].glob("*.tar.gz"))
    assert len(backups) == 1
    with tarfile.open(backups[0]) as tf:
        names = tf.getnames()
    assert "tags" in names or any("uuid-1.tag" in n for n in names)


def test_does_not_delete_old_sources(layout):
    tag_file = layout["tags_dir"] / "uuid-1.tag"
    tag_file.write_text("my-feature\n")
    layout["mutes_file"].write_text(json.dumps({"a": "2026-07-01"}))
    run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert tag_file.exists()
    assert layout["mutes_file"].exists()


def test_missing_sources_are_a_no_op_not_an_error(layout):
    """No tags dir, no mutes file, no roots with sessions — migration succeeds
    with zero rows written."""
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    assert sessions_db.list_sessions(path=layout["db_path"]) == []


def test_run_twice_is_idempotent(layout):
    (layout["tags_dir"] / "uuid-1.tag").write_text("my-feature\n")
    sess = _make_session_dir(layout["root"], "myproj", "20260713-twice")
    (sess / ".last-opened").touch()
    run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    rows = sessions_db.list_sessions(path=layout["db_path"])
    assert len(rows) == 1  # not duplicated
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_migrate_sessions_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cc_session_tools.cli.migrate_sessions_db'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/cli/migrate_sessions_db.py
"""One-shot migration: flat .tag cache + activity sentinels + doctor-mutes JSON
-> sessions.db.

Generated by: src/cc_session_tools/cli/migrate_sessions_db.py
Exposed via: ccst sessions migrate [--dry-run] [--sessions-db <path>]
             [--tags-dir <path>] [--mutes-file <path>]

Replaces the now-retired `ccst tags migrate` (see design decision D4 in
docs/superpowers/plans/2026-07-13-data-store-uplift-04-sessions-db.md) — this
migrates from all three legacy sources at once, not just the tag cache.

Non-destructive per docs/superpowers/plans/2026-07-13-data-store-uplift-00-overview.md
Section "Cross-phase decisions" #4:
  1. Write sessions.db without touching any old flat file.
  2. Verify: row counts match what was migrated.
  3. tar czf backup of the pre-migration flat-file sources.
  4. Only then print the rm command for the user to run by hand — this script
     never deletes anything itself, matching migrate_session_tags.py's own
     precedent. Activity-sentinel files (.last-opened/.last-active under each
     cc-sessions/<basename>/) are left in place entirely; they are harmless
     once sessions.db is authoritative and ccs/ccr no longer read them.
"""
from __future__ import annotations

import argparse
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import doctor_mutes, sessions_db
from cc_session_tools.lib.roots import RootsConfigError, load_session_roots
from cc_session_tools.lib.sessions import iter_sessions, session_start_date

DEFAULT_TAGS_DIR = Path.home() / ".cache" / "claude" / "session-tags"
DEFAULT_MUTES_FILE = Path.home() / ".claude" / "cc-doctor-mutes.json"


def _migrate_tags(tags_dir: Path, *, db_path: Path, dry_run: bool) -> tuple[int, int]:
    """Return (source_count, migrated_count)."""
    tag_files = sorted(tags_dir.glob("*.tag")) if tags_dir.is_dir() else []
    migrated = 0
    for f in tag_files:
        uuid = f.stem
        try:
            tag = f.read_text().strip()
        except OSError as exc:
            print(f"  ERROR reading {f}: {exc}", file=sys.stderr)
            continue
        if not tag:
            continue
        if dry_run:
            print(f"  would migrate tag: {uuid} -> {tag!r}")
        else:
            sessions_db.write_tag(uuid, tag, path=db_path)
        migrated += 1
    return len(tag_files), migrated


def _migrate_activity(roots: list[Path], *, db_path: Path, dry_run: bool) -> tuple[int, int]:
    """Walk cc-sessions/<basename>/ dirs under each root's projects and copy
    .last-opened / .last-active sentinel mtimes into the sessions table.
    Returns (source_session_dir_count, migrated_count)."""
    source_count = 0
    migrated = 0
    for root in roots:
        if not root.is_dir():
            continue
        for proj in root.iterdir():
            if not proj.is_dir():
                continue
            cc = proj / "cc-sessions"
            for sess in iter_sessions(cc):
                basename = sess.name
                if session_start_date(basename) is None:
                    continue
                source_count += 1
                opened_file = sess / ".last-opened"
                active_file = sess / ".last-active"
                opened_mtime = opened_file.stat().st_mtime if opened_file.is_file() else None
                active_mtime = active_file.stat().st_mtime if active_file.is_file() else None
                if dry_run:
                    print(
                        f"  would migrate session: {proj / 'cc-sessions' / basename} "
                        f"(opened={opened_mtime}, active={active_mtime})"
                    )
                else:
                    sessions_db.ensure_session_row(proj, basename, path=db_path)
                    if opened_mtime is not None:
                        sessions_db.touch_last_opened(proj, basename, path=db_path, when=opened_mtime)
                    if active_mtime is not None:
                        sessions_db.touch_last_active(proj, basename, path=db_path, when=active_mtime)
                migrated += 1
    return source_count, migrated


def _migrate_mutes(mutes_file: Path, *, db_path: Path, dry_run: bool) -> tuple[int, int]:
    if not mutes_file.is_file():
        return 0, 0
    try:
        data = json.loads(mutes_file.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ERROR reading {mutes_file}: {exc}", file=sys.stderr)
        return 0, 0
    if not isinstance(data, dict):
        return 0, 0
    migrated = 0
    for name, muted_at in data.items():
        if dry_run:
            print(f"  would migrate mute: {name!r} (muted {muted_at})")
        else:
            doctor_mutes.add_mute(db_path, str(name), today=str(muted_at))
        migrated += 1
    return len(data), migrated


def _tar_backup(sources: list[Path], *, backup_dir: Path) -> Path | None:
    existing = [p for p in sources if p.exists()]
    if not existing:
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"sessions-migration-{stamp}.tar.gz"
    with tarfile.open(dest, "w:gz") as tf:
        for p in existing:
            tf.add(p, arcname=p.name)
    return dest


def run_migration(
    *,
    dry_run: bool,
    db_path: Path,
    tags_dir: Path,
    mutes_file: Path,
    roots: list[Path],
    backup_dir: Path,
) -> int:
    print(f"Sessions DB  : {db_path}")
    print(f"Tags source  : {tags_dir}")
    print(f"Mutes source : {mutes_file}")
    print(f"Roots        : {', '.join(str(r) for r in roots) or '(none configured)'}")
    if dry_run:
        print("(dry-run mode — no files will be written)")
    print()

    print("Tags:")
    tag_src, tag_migrated = _migrate_tags(tags_dir, db_path=db_path, dry_run=dry_run)
    print(f"  {tag_migrated}/{tag_src} migrated")
    print()

    print("Activity sentinels:")
    sess_src, sess_migrated = _migrate_activity(roots, db_path=db_path, dry_run=dry_run)
    print(f"  {sess_migrated}/{sess_src} session dirs migrated")
    print()

    print("Doctor mutes:")
    mute_src, mute_migrated = _migrate_mutes(mutes_file, db_path=db_path, dry_run=dry_run)
    print(f"  {mute_migrated}/{mute_src} migrated")
    print()

    if dry_run:
        print("Dry-run complete — no files were written or backed up.")
        return 0

    problems: list[str] = []
    if len(sessions_db.list_sessions(path=db_path)) < sess_migrated:
        problems.append("sessions table row count is lower than migrated count")
    migrated_tags = sessions_db.lookup_tags(
        [f.stem for f in tags_dir.glob("*.tag")] if tags_dir.is_dir() else [], path=db_path
    )
    if len(migrated_tags) < tag_migrated:
        problems.append("session_tags row count is lower than migrated count")
    if len(doctor_mutes.load_mutes(db_path)) < mute_migrated:
        problems.append("doctor_mutes row count is lower than migrated count")

    if problems:
        for p in problems:
            print(f"  VERIFY FAILED: {p}", file=sys.stderr)
        print(
            "Verification failed — old flat files were NOT backed up or removed. "
            "sessions.db has been written but may be incomplete; re-run after "
            "investigating.",
            file=sys.stderr,
        )
        return 1

    backup_path = _tar_backup([tags_dir, mutes_file], backup_dir=backup_dir)
    if backup_path:
        print(f"Backup written: {backup_path}")

    print()
    print("Verification passed. Review the output above. To remove the old flat-file")
    print("sources once satisfied, run:")
    if tags_dir.is_dir():
        print(f"  rm -rf {tags_dir}")
    if mutes_file.is_file():
        print(f"  rm {mutes_file}")
    print(
        "  (activity sentinels — .last-opened/.last-active files under each "
        "cc-sessions/<basename>/ — are intentionally left in place; they are "
        "harmless once sessions.db is authoritative.)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "One-shot migration of the flat tag cache, activity sentinels, and "
            "cc-doctor-mutes.json into sessions.db. Non-destructive — old files "
            "are never deleted automatically."
        )
    )
    ap.add_argument("--dry-run", action="store_true",
                     help="Print what would be migrated without writing anything.")
    ap.add_argument("--sessions-db", default=None, metavar="PATH",
                     help="Destination sessions.db path (default: from CCST_SESSIONS_DIR "
                          "or ~/.local/share/claude/sessions.db)")
    ap.add_argument("--tags-dir", default=None, metavar="PATH",
                     help=f"Source flat tags dir (default: {DEFAULT_TAGS_DIR})")
    ap.add_argument("--mutes-file", default=None, metavar="PATH",
                     help=f"Source doctor-mutes JSON file (default: {DEFAULT_MUTES_FILE})")
    args = ap.parse_args(argv)

    db_path = Path(args.sessions_db) if args.sessions_db else sessions_db.default_db_path()
    tags_dir = Path(args.tags_dir) if args.tags_dir else DEFAULT_TAGS_DIR
    mutes_file = Path(args.mutes_file) if args.mutes_file else DEFAULT_MUTES_FILE

    try:
        roots = load_session_roots()
    except RootsConfigError as e:
        print(str(e), file=sys.stderr)
        return 1

    backup_dir = sessions_db.default_db_path().parent / "migration-backups"
    return run_migration(
        dry_run=args.dry_run, db_path=db_path, tags_dir=tags_dir,
        mutes_file=mutes_file, roots=roots, backup_dir=backup_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_migrate_sessions_db.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/migrate_sessions_db.py tests/test_migrate_sessions_db.py
git commit -m "feat(sessions-db): add one-shot migration script (tags + activity + mutes -> sessions.db)"
```

---

## Task 14: `ccst.py` wiring — retire `tags migrate`, add `sessions migrate`/`sessions list`

Satisfies this repo's `CLAUDE.md` data-store convention: "Ship a corresponding ccst query
subcommand for its common read operations before being considered done" — `sessions list` is that
subcommand for `sessions.db`.

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`
- Delete: `src/cc_session_tools/cli/migrate_session_tags.py`
- Create: `tests/test_ccst_sessions_cli.py`

- [ ] **Step 1: Update the module docstring's command list**

Replace lines 21-24 (`tags migrate ...`) with:

```
  sessions migrate               One-shot migration of the flat tag cache,
                                 activity sentinels, and cc-doctor-mutes.json
                                 into sessions.db. Non-destructive; never
                                 deletes old files automatically.
  sessions list                  List all sessions recorded in sessions.db
                                 (debug/inspection; --json for scripting).
```

- [ ] **Step 2: Remove the `tags migrate` command function and add the two new ones**

Delete `_cmd_tags_migrate` (original lines 771-784). In its place:

```python
# ---------- sessions migrate / list ----------


def _cmd_sessions_migrate(args: argparse.Namespace) -> int:
    from cc_session_tools.cli.migrate_sessions_db import DEFAULT_MUTES_FILE, DEFAULT_TAGS_DIR, run_migration
    from cc_session_tools.lib import sessions_db
    from cc_session_tools.lib.roots import RootsConfigError, load_session_roots

    db_path = Path(args.sessions_db) if args.sessions_db else sessions_db.default_db_path()
    tags_dir = Path(args.tags_dir) if args.tags_dir else DEFAULT_TAGS_DIR
    mutes_file = Path(args.mutes_file) if args.mutes_file else DEFAULT_MUTES_FILE

    try:
        roots = load_session_roots()
    except RootsConfigError as e:
        print(str(e), file=sys.stderr)
        return 1

    backup_dir = sessions_db.default_db_path().parent / "migration-backups"
    return run_migration(
        dry_run=args.dry_run, db_path=db_path, tags_dir=tags_dir,
        mutes_file=mutes_file, roots=roots, backup_dir=backup_dir,
    )


def _cmd_sessions_list(args: argparse.Namespace) -> int:
    from cc_session_tools.lib import sessions_db

    db_path = Path(args.sessions_db) if args.sessions_db else None
    rows = sessions_db.list_sessions(path=db_path)
    if not rows:
        print("No sessions recorded in sessions.db.")
        return 0

    rows = sorted(rows, key=lambda r: r.start_date, reverse=True)
    if args.json:
        import json as _json
        print(_json.dumps([
            {
                "basename": r.basename,
                "project_dir": str(r.project_dir),
                "start_date": r.start_date,
                "last_opened": r.last_opened,
                "last_active": r.last_active,
            }
            for r in rows
        ]))
        return 0

    name_w = max(len(r.basename) for r in rows)
    for r in rows:
        print(
            f"{r.basename:<{name_w}}  "
            f"opened={_fmt_ts(r.last_opened)}  active={_fmt_ts(r.last_active)}  "
            f"{r.project_dir}"
        )
    return 0


def _fmt_ts(epoch: float) -> str:
    if not epoch:
        return "(never)"
    import datetime as _dt
    return _dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")
```

- [ ] **Step 3: Update `main()`'s dispatch table**

Replace (original lines 1282-1284):

```python
    if args.noun == "tags":
        if args.verb == "migrate":
            sys.exit(_cmd_tags_migrate(args))
```

with:

```python
    if args.noun == "sessions":
        if args.verb == "migrate":
            sys.exit(_cmd_sessions_migrate(args))
        if args.verb == "list":
            sys.exit(_cmd_sessions_list(args))
```

- [ ] **Step 4: Replace the `tags` argparse block with `sessions`**

Replace the `# ---- tags ----` block (original lines 1175-1206):

```python
    # ---- sessions ----
    sessions_parser = sub.add_parser("sessions", help="sessions.db management commands")
    sessions_sub = sessions_parser.add_subparsers(dest="verb", metavar="<verb>")
    sessions_sub.required = True

    sessions_migrate_parser = sessions_sub.add_parser(
        "migrate",
        help=(
            "One-shot migration of the flat tag cache, activity sentinels, and "
            "cc-doctor-mutes.json into sessions.db. Non-destructive — never "
            "deletes old files automatically."
        ),
    )
    sessions_migrate_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be migrated without writing anything.",
    )
    sessions_migrate_parser.add_argument(
        "--sessions-db", default=None, metavar="PATH",
        help="Destination sessions.db path (default: from CCST_SESSIONS_DIR or "
             "~/.local/share/claude/sessions.db)",
    )
    sessions_migrate_parser.add_argument(
        "--tags-dir", default=None, metavar="PATH",
        help="Source flat tags dir (default: ~/.cache/claude/session-tags/)",
    )
    sessions_migrate_parser.add_argument(
        "--mutes-file", default=None, metavar="PATH",
        help="Source doctor-mutes JSON file (default: ~/.claude/cc-doctor-mutes.json)",
    )

    sessions_list_parser = sessions_sub.add_parser(
        "list",
        help="List all sessions recorded in sessions.db (debug/inspection).",
    )
    sessions_list_parser.add_argument(
        "--sessions-db", default=None, metavar="PATH",
        help="sessions.db path override (default: from CCST_SESSIONS_DIR)",
    )
    sessions_list_parser.add_argument(
        "--json", action="store_true",
        help="Output as a JSON array instead of a formatted table.",
    )
```

- [ ] **Step 5: Update the doctor `--mutes-file` help text (D7)**

Replace (original line 1057):

```python
        help="Mute-store path (default: ~/.claude/cc-doctor-mutes.json)",
```

with:

```python
        help="Mute-store sessions.db path (default: ~/.local/share/claude/sessions.db, "
             "or $CCST_SESSIONS_DIR)",
```

- [ ] **Step 6: Delete `migrate_session_tags.py`**

```bash
git rm src/cc_session_tools/cli/migrate_session_tags.py
```

(No test file exists for it — confirmed zero coverage during this plan's research — so no test file
to delete.)

- [ ] **Step 7: Write CLI-level tests for the two new subcommands**

```python
# tests/test_ccst_sessions_cli.py
"""Tests for `ccst sessions migrate` and `ccst sessions list`."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent.parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path, monkeypatch):
    import os
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(tmp_path / "repos")
    (tmp_path / "repos").mkdir()
    env["CCST_SESSIONS_DIR"] = str(tmp_path / "db")
    return env


def test_sessions_list_empty_db(base_env):
    r = _run(base_env, "sessions", "list")
    assert r.returncode == 0
    assert "No sessions recorded" in r.stdout


def test_sessions_migrate_dry_run_no_sources(base_env):
    r = _run(base_env, "sessions", "migrate", "--dry-run")
    assert r.returncode == 0
    assert "dry-run mode" in r.stdout


def test_sessions_migrate_then_list_shows_row(base_env, tmp_path):
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    (tags_dir / "uuid-1.tag").write_text("my-feature\n")
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    sess = proj / "cc-sessions" / "20260713-my-feature"
    (sess / "working").mkdir(parents=True)

    r_migrate = _run(base_env, "sessions", "migrate", "--tags-dir", str(tags_dir))
    assert r_migrate.returncode == 0

    r_list = _run(base_env, "sessions", "list")
    assert r_list.returncode == 0
    assert "20260713-my-feature" in r_list.stdout


def test_sessions_list_json_output(base_env, tmp_path):
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    (proj / "cc-sessions" / "20260713-json-test" / "working").mkdir(parents=True)
    _run(base_env, "sessions", "migrate")

    r = _run(base_env, "sessions", "list", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert any(row["basename"] == "20260713-json-test" for row in data)


def test_tags_noun_no_longer_exists(base_env):
    """D4: `ccst tags migrate` is retired."""
    r = _run(base_env, "tags", "migrate")
    assert r.returncode != 0
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
uv run pytest tests/test_ccst_sessions_cli.py -v
```

Expected: PASS (6 tests)

- [ ] **Step 9: Grep-verify no other references to the retired command remain**

```bash
grep -rn "tags migrate\|migrate_session_tags\|_cmd_tags_migrate" src/ tests/
```

Expected: no output (empty).

- [ ] **Step 10: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_sessions_cli.py
git rm src/cc_session_tools/cli/migrate_session_tags.py
git commit -m "feat(sessions-db): retire ccst tags migrate, add ccst sessions migrate/list (D4)"
```

---

## Task 15: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest -q
```

Expected: all tests pass — no pre-existing failures, no regressions in any file this phase touched
or left untouched (in particular `tests/test_ccst_gc_report.py`, `tests/test_session_names.py`,
`tests/test_ccr_orphans.py`'s non-Task-12 tests, and `tests/test_ccst_cli.py` should all be
unaffected).

- [ ] **Step 2: Run the linter/type-checker**

```bash
uv run ruff check src/cc_session_tools/lib/sessions_db.py src/cc_session_tools/lib/doctor_mutes.py \
  src/cc_session_tools/cli/migrate_sessions_db.py src/cccs_hooks/session_tag.py \
  src/cccs_hooks/after_response.py src/cc_session_tools/lib/sessions.py \
  src/cc_session_tools/cli/ccd.py src/cc_session_tools/cli/ccs.py src/cc_session_tools/cli/ccr.py \
  src/cc_session_tools/cli/ccst.py
uv run mypy src/cc_session_tools/lib/sessions_db.py src/cc_session_tools/lib/doctor_mutes.py \
  src/cc_session_tools/cli/migrate_sessions_db.py src/cccs_hooks/session_tag.py \
  src/cccs_hooks/after_response.py src/cc_session_tools/lib/sessions.py \
  src/cc_session_tools/cli/ccd.py src/cc_session_tools/cli/ccs.py src/cc_session_tools/cli/ccr.py \
  src/cc_session_tools/cli/ccst.py
```

(Check `pyproject.toml`/CI config first for the exact configured invocation if these differ — CI
today does not run ruff/mypy as a gate, but `mypy>=1.10`/`pytest-mock` are declared dev deps, so run
both anyway and fix anything they report; do not skip a check that exists in the repo's tooling just
because CI doesn't enforce it yet.)

Expected: zero errors. Fix any `Any`/untyped-dict/missing-annotation issues before proceeding — every
new function in `sessions_db.py`, `migrate_sessions_db.py`, and the rewritten hooks must be fully
type-annotated per this repo's coding standards.

- [ ] **Step 3: Confirm the CLI install-check still passes**

```bash
uv run python -m cc_session_tools.cli.ccst --help
uv run python -m cc_session_tools.cli.ccst sessions --help
uv run python -m cc_session_tools.cli.ccst sessions migrate --help
uv run python -m cc_session_tools.cli.ccst sessions list --help
uv run python -m cc_session_tools.cli.ccr --help
uv run python -m cc_session_tools.cli.ccs --help
```

Expected: all print help text without error; no `tags` noun appears anywhere in
`ccst --help`'s output.

- [ ] **Step 4: No version bump in this phase**

Per `overview.md`, the `0.18.0` → `0.19.0` bump and `CHANGELOG.md` entry covering the *entire*
data-store migration happen once, as the last step of **Phase 7** — do not bump `pyproject.toml`'s
version here. This phase's commits land on the shared migration branch without a release cut.

## Handoff

Phase 4 is complete when:

- `sessions.db` (schema: `session_tags`, `sessions`, `doctor_mutes`) is live, created on first
  connection by any of `session_tag.py`, `after_response.py`, `ccd.py`, `doctor_mutes.py`, or
  `ccst sessions migrate`.
- `ccl --global`/`ccs`/`ccr` enumerate and match sessions via one indexed `sessions.db` query instead
  of an O(roots × projects × sessions) filesystem walk on every invocation (items 1 and 3 of the
  brief's perf list); `--order-by opened`/`active` read straight from DB columns with zero
  per-session filesystem stats (the other half of item 2). `--order-by update`'s `rglob` walk is
  unchanged by design (D1). **This is the explicit, testable delivery of the design spec's
  2026-07-13 performance requirement** ("`ccl`/`ccr`/`ccs` must be measurably faster at listing
  and matching sessions by title/tag") — enforced by Task 10's
  `TestSessionEnumerationScaling` regression tests (flat cost as session count grows, sub-second
  at 2000 sessions), scoped explicitly to titles/tags/metadata, not session content search.
  `ccr` shares the identical `sessions_db.list_sessions()`/matching query path built in Task 10
  (wired in Task 12), so it inherits the same performance characteristic without a duplicate
  benchmark.
- `ccst tags migrate` no longer exists; `ccst sessions migrate` (non-destructive, dry-run capable)
  and `ccst sessions list` (the CLAUDE.md-mandated query subcommand) exist in its place.
- `ccst doctor --mute/--unmute/--list-mutes/--drift` behave identically to before, now backed by
  `sessions.db`'s `doctor_mutes` table, with net-new CLI-level test coverage.
- A human operator has run `ccst sessions migrate` once on each machine that had existing `.tag`
  files, `.last-opened`/`.last-active` sentinels, or a `cc-doctor-mutes.json` — this is a **manual,
  one-time step**, not part of `ccst install`, per `overview.md`'s migration-script convention. Until
  that run happens on a given machine, `ccs`/`ccl`/`ccr` will report zero sessions even though
  `cc-sessions/` directories exist on disk (documented consequence of D-decisions above, not a bug).
- `docs/superpowers/plans/2026-07-13-data-store-uplift-05-telemetry.md` (Phase 5) and the other
  parallel phases are unaffected by anything in this plan — no shared files were touched outside the
  ones listed in File Structure.
