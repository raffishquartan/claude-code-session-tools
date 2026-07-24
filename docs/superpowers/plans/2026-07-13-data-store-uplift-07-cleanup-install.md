# Phase 7: Cleanup and integration — install/doctor hookup, gc report, docs, version bump

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Read `2026-07-13-data-store-uplift-00-overview.md` first — it fixes the env-var conventions,
> the `db.connect()` contract, and the "each store creates its own schema on first access"
> decision this plan integrates against. **This is the final phase.** It assumes Phases 1-6 are
> already merged into this branch: `lib/db.py`/`lib/paths.py` exist; `ccmsg` reads/writes
> `ccmsg.db`; `ccsched` reads/writes `ccsched.db`; `sessions.db` exists and `ccl`/`ccr` query it;
> `telemetry.db` exists with `ccst telemetry query`; `command-cache.db` and `claude-flags.json`
> have moved under `~/.local/share/claude/`. This phase does not redo any of that work — only
> integrates and closes out loose ends.

**Goal:** wire the six new/moved data stores into `ccst doctor` and `ccst gc report`, write the
manual tar-backup safety-net checklist, update every stale doc reference to old paths, cut a
`CHANGELOG.md` entry, and bump the version to `0.19.0` — closing out the whole data-store
SQLite migration.

**Architecture:** five independent workstreams (Tasks 1-5 below), each its own self-contained
task group with its own commits. Workstreams 1 and 3 touch existing pure-logic modules
(`lib/doctor.py`, `lib/session_gc.py`) plus their CLI wiring in `ccst.py`; workstream 2 is
verification-only (no code change expected); workstream 4 produces one new reference doc;
workstream 5 touches `README.md`, one historical plan doc, this repo's own `.claude/CLAUDE.md`,
`CHANGELOG.md`, and `pyproject.toml`.

**Tech Stack:** Python 3.11 stdlib (`sqlite3`, `json`, `os`, `pathlib`), pytest, `monkeypatch`,
`sqlite3` CLI (for live-schema inspection during Task 3).

---

## A note on schema assumptions in this plan

This plan was written while Phases 2-5 (`ccmsg.db`, `ccsched.db`, `sessions.db`, `telemetry.db`)
were being drafted in parallel by sibling agents, working only from the overview's fixed
contract (env vars, filenames, `db.connect()` signature) — not from those phases' actual table
schemas, which did not exist yet. By the time this phase is *executed*, Phases 1-6 will already
be merged and their real schemas will be readable from the live source tree.

Every task below that touches another phase's `.db` file (Task 1's `check_data_stores`, Task 3's
new `gc report` extractors) therefore starts with an explicit **"read the live schema first"**
step. The SQL and table/column names shown in this plan's code blocks are the design spec's
*documented intent* (quoted inline where relevant), not verified live schema — treat them as a
strong starting draft, not gospel. If what you find on disk differs, adjust the SQL and the
test fixtures to match; the parts of each task that are NOT allowed to drift are: the
`CheckResult`/`StoreReport`/`GcReport` shapes (unchanged, confirmed store-agnostic below), the
CLI flag names (unchanged — hard constraint from the overview), and the WARN/FAIL semantics
described in each task.

---

## File Structure

- Modify: `src/cc_session_tools/lib/doctor.py` (add `check_data_stores()`, wire into `run_all_checks()`)
- Modify: `src/cc_session_tools/cli/ccst.py` (`_cmd_doctor` builds the six store paths; `gc report` gains `--sessions-dir`)
- Modify: `src/cc_session_tools/lib/session_gc.py` (replace flat-file scheduler/messages extractors with DB-backed ones; add a `sessions-index` store)
- Modify: `tests/test_ccst_doctor.py`
- Modify: `tests/test_ccst_gc_report.py`
- Create: `docs/data-store-migration-backups.md`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-06-20-inter-session-messaging.md`
- Modify: `.claude/CLAUDE.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`

No other files change in this phase.

---

## Task 1: `ccst doctor` — verify all six data stores are reachable

**Files:**
- Modify: `src/cc_session_tools/lib/doctor.py`
- Modify: `src/cc_session_tools/cli/ccst.py:555-637` (`_cmd_doctor`)
- Test: `tests/test_ccst_doctor.py`

Today `doctor.py` has six check functions (`check_cli_on_path`, `check_env_dir`,
`check_settings_json`, `check_hook_registered`, `check_skill_symlink`, `check_pypi_version`) run by
`run_all_checks()`. No check touches any data store. This task adds `check_data_stores()` — the
"closest equivalent to an install-time verification step" chosen in the overview's binding
decision 8, since every store creates its own schema lazily on first `db.connect(path,
ddl=...)` rather than through a dedicated provisioning step.

### Step 1: Read the live store-path accessors

Every subsystem already exposes (or will expose, once Phases 2-6 are merged) a small accessor
function resolving its own env-var override, matching the existing `scheduler_dir()` /
`store_root()` pattern. Confirm the exact names before writing code:

```bash
grep -rn "def scheduler_dir\|def db_path" src/cc_session_tools/lib/scheduler/store.py   # Phase 3 MOVED scheduler_dir here from state.py
grep -rn "def store_root" src/cc_session_tools/lib/messaging/store.py
grep -rn "def default_db_path\|CCST_SESSIONS_DIR" src/cc_session_tools/lib/sessions_db.py  # Phase 4's sessions.db accessor
grep -rn "def db_path\|def hooks_dir\|CCCS_HOOKS_DIR" src/cc_session_tools/lib/telemetry_store.py  # Phase 5's telemetry.db accessor (MOVED out of cccs_hooks/telemetry.py)
grep -rn "def _db_path\|CCCS_CACHE_DB" src/cccs_hooks/cache.py  # Phase 6's command-cache.db accessor
grep -rn "def _cache_file\|def _cache_dir\|CCST_CLAUDE_FLAGS_DIR" src/cc_session_tools/lib/claude_flags.py  # Phase 6's claude-flags.json accessor
```

The verified accessors (confirmed against the merged Phase 2-6 source; these are the exact names
Step 7 imports below):

| Store | Module | Accessor | Returns |
|---|---|---|---|
| `ccmsg` | `cc_session_tools.lib.messaging.store` | `store_root()` | directory (append `/ "ccmsg.db"`) |
| `ccsched` | `cc_session_tools.lib.scheduler.store` | `scheduler_dir()` (also `db_path()`) | directory (append `/ "ccsched.db"`) — **note: moved here from `scheduler.state` in Phase 3** |
| `sessions` | `cc_session_tools.lib.sessions_db` | `default_db_path()` | full `.../sessions.db` path (already includes the filename; honours `CCST_SESSIONS_DIR`) |
| `telemetry` | `cc_session_tools.lib.telemetry_store` | `db_path()` (also `hooks_dir()`) | full `.../telemetry.db` path — **note: lives in `telemetry_store`, not `cccs_hooks.telemetry`** |
| `command-cache` | `cccs_hooks.cache` | `_db_path()` | full `.../command-cache.db` path — **note: Phase 6 deleted the old `_DEFAULT_DB` constant; this replaces it** |
| `claude-flags` | `cc_session_tools.lib.claude_flags` | `_cache_file()` (dir via `_cache_dir()`) | full `.../claude-flags.json` path |

`_db_path()` and `_cache_file()` are private (leading-underscore) module accessors — Phase 6 exposes
no public equivalent. Importing them across modules for this read-only health check is acceptable
(they are the only path-construction those phases expose); a future cleanup could promote them to
public aliases, but that is out of scope for this phase.

### Step 2: Write the failing tests

```python
# append to tests/test_ccst_doctor.py
import sqlite3

from cc_session_tools.lib.doctor import check_data_stores
from cc_session_tools.lib import db as _db


# ---------- check_data_stores ----------

_DDL = "CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY);"


def test_check_data_stores_ok_for_valid_existing_db(tmp_path: Path) -> None:
    target = tmp_path / "ccmsg.db"
    _db.connect(target, ddl=_DDL).close()

    results = check_data_stores({"ccmsg": target})

    assert len(results) == 1
    assert results[0].name == "data-store:ccmsg"
    assert results[0].status == Status.OK


def test_check_data_stores_ok_for_valid_existing_json(tmp_path: Path) -> None:
    target = tmp_path / "claude-flags.json"
    target.write_text('{"mtime": 1.0, "path": "/x", "flags": []}')

    results = check_data_stores({"claude-flags": target})

    assert results[0].status == Status.OK


def test_check_data_stores_fail_for_corrupt_db(tmp_path: Path) -> None:
    target = tmp_path / "ccsched.db"
    target.write_bytes(b"not a sqlite file at all")

    results = check_data_stores({"ccsched": target})

    assert results[0].status == Status.FAIL
    assert "ccsched.db" in results[0].reason


def test_check_data_stores_fail_for_corrupt_json(tmp_path: Path) -> None:
    target = tmp_path / "claude-flags.json"
    target.write_text("{not valid json")

    results = check_data_stores({"claude-flags": target})

    assert results[0].status == Status.FAIL


def test_check_data_stores_warn_when_missing_but_parent_writable(tmp_path: Path) -> None:
    target = tmp_path / "not-created-yet" / "sessions.db"

    results = check_data_stores({"sessions": target})

    assert results[0].status == Status.WARN
    assert "will be created" in results[0].reason


def test_check_data_stores_fail_when_missing_and_ancestor_unwritable(tmp_path: Path) -> None:
    readonly_root = tmp_path / "readonly"
    readonly_root.mkdir()
    readonly_root.chmod(0o500)
    target = readonly_root / "telemetry.db"
    try:
        results = check_data_stores({"telemetry": target})
        assert results[0].status == Status.FAIL
    finally:
        readonly_root.chmod(0o700)  # allow tmp_path cleanup


def test_check_data_stores_handles_multiple_stores_independently(tmp_path: Path) -> None:
    good = tmp_path / "ccmsg.db"
    _db.connect(good, ddl=_DDL).close()
    bad = tmp_path / "ccsched.db"
    bad.write_bytes(b"garbage")

    results = check_data_stores({"ccmsg": good, "ccsched": bad})

    by_name = {r.name: r for r in results}
    assert by_name["data-store:ccmsg"].status == Status.OK
    assert by_name["data-store:ccsched"].status == Status.FAIL


# ---------- run_all_checks wiring ----------

def test_run_all_checks_includes_data_store_checks_when_store_paths_given(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    store = tmp_path / "ccmsg.db"
    _db.connect(store, ddl=_DDL).close()

    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
        store_paths={"ccmsg": store},
    )

    assert any(r.name == "data-store:ccmsg" for r in results)


def test_run_all_checks_skips_data_store_checks_when_omitted(tmp_path: Path) -> None:
    """store_paths defaults to None — existing callers that don't pass it are unaffected."""
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
    )

    assert not any(r.name.startswith("data-store:") for r in results)
```

Also add `check_data_stores` to the existing `from cc_session_tools.lib.doctor import (...)` block
at the top of `tests/test_ccst_doctor.py`.

### Step 3: Run tests to verify they fail

Run: `uv run pytest tests/test_ccst_doctor.py -k data_store -v`
Expected: FAIL with `ImportError: cannot import name 'check_data_stores'`

### Step 4: Implement `check_data_stores()`

```python
# src/cc_session_tools/lib/doctor.py — add near the top
import os
import sqlite3

from cc_session_tools.lib.db import connect as _db_connect
```

```python
# src/cc_session_tools/lib/doctor.py — add after check_pypi_version, before
# "---------- high-level runner ----------"


def _nearest_existing_ancestor(path: Path) -> Path:
    """Walk up from ``path`` to the first directory that actually exists."""
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:  # reached filesystem root without finding one
            return current
        current = parent
    return current


def check_data_stores(store_paths: dict[str, Path]) -> list[CheckResult]:
    """Attempt to open each per-subsystem data store under ``data_home()``.

    ``store_paths`` maps a short store name (e.g. ``"ccmsg"``) to its resolved
    file path (e.g. ``data_home() / "ccmsg.db"``, already accounting for that
    subsystem's own env-var override) — this function never resolves paths
    itself, matching every other check in this module ("all filesystem paths
    are passed in").

    For a path ending in ``.db``: if it exists, opens it read-only via
    :func:`cc_session_tools.lib.db.connect` — a FAIL means the file is
    present but not a valid/openable SQLite database (corruption,
    permissions, wrong schema version). If it does not exist yet, this is
    expected before first use — every store creates its own schema on first
    real ``connect(path, ddl=...)`` call (data-store-uplift overview, binding
    decision 8) — so this WARNs rather than FAILs, unless the nearest
    existing ancestor directory is not writable, in which case first use
    would also fail, so this FAILs instead.

    For a path not ending in ``.db`` (``claude-flags.json`` is a flat JSON
    file, not a SQLite store — see Phase 6): the same
    exists/invalid-is-FAIL, missing/WARN-unless-unwritable-ancestor
    treatment applies, but "invalid" is checked via ``json.load`` instead of
    ``db.connect``.
    """
    results: list[CheckResult] = []
    for store_name, path in store_paths.items():
        name = f"data-store:{store_name}"
        if path.exists():
            try:
                if path.suffix == ".db":
                    conn = _db_connect(path, readonly=True)
                    conn.execute("PRAGMA schema_version").fetchone()
                    conn.close()
                else:
                    with path.open() as f:
                        json.load(f)
                results.append(CheckResult(name=name, status=Status.OK, reason=str(path)))
            except (sqlite3.DatabaseError, sqlite3.OperationalError, json.JSONDecodeError, OSError) as e:
                results.append(
                    CheckResult(
                        name=name,
                        status=Status.FAIL,
                        reason=f"{path} exists but failed to open: {e}",
                    )
                )
            continue

        ancestor = _nearest_existing_ancestor(path)
        if os.access(ancestor, os.W_OK):
            results.append(
                CheckResult(
                    name=name,
                    status=Status.WARN,
                    reason=f"not yet created; will be created at {path} on first use",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=name,
                    status=Status.FAIL,
                    reason=f"not yet created and {ancestor} is not writable",
                )
            )
    return results
```

### Step 5: Wire into `run_all_checks()`

```python
# src/cc_session_tools/lib/doctor.py — modify run_all_checks() signature and body
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
) -> list[CheckResult]:
```

Add, immediately after the skill-symlink block and before the `# PyPI version check` block:

```python
    # Data stores
    if store_paths is not None:
        results.extend(check_data_stores(store_paths))
```

Update the function's docstring to document the new `store_paths` parameter (mirrors the existing
`Parameters` block style — `store_paths: dict mapping short store name -> resolved file path; when
None, data-store checks are skipped (used by callers/tests that don't care about them)`).

### Step 6: Run tests to verify they pass

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: PASS (all existing + 9 new tests)

### Step 7: Wire `_cmd_doctor` to build the six store paths

Read `src/cc_session_tools/cli/ccst.py:555-637` (`_cmd_doctor`) first — the `results = run_all_checks(...)` call sits at line 615. Add the six-store dict just before it, using whatever accessors Step 1 confirmed:

```python
# src/cc_session_tools/cli/ccst.py — inside _cmd_doctor, before `results = run_all_checks(...)`
from cc_session_tools.lib.scheduler.store import scheduler_dir      # Phase 3 (moved here from .state)
from cc_session_tools.lib.messaging.store import store_root         # Phase 2
from cc_session_tools.lib.sessions_db import default_db_path as sessions_db_path  # Phase 4 (full .db path)
from cc_session_tools.lib import telemetry_store                    # Phase 5 (db_path() -> full .db path)
from cccs_hooks.cache import _db_path as command_cache_db_path      # Phase 6 (replaces deleted _DEFAULT_DB)
from cc_session_tools.lib.claude_flags import _cache_file as claude_flags_file  # Phase 6 (full .json path)

store_paths = {
    "ccmsg": store_root() / "ccmsg.db",
    "ccsched": scheduler_dir() / "ccsched.db",
    "sessions": sessions_db_path(),
    "telemetry": telemetry_store.db_path(),
    "command-cache": command_cache_db_path(),
    "claude-flags": claude_flags_file(),
}
```

Every accessor above was verified against the merged Phase 2-6 source (Step 1's table). Three
return a full file path already (`default_db_path()`, `telemetry_store.db_path()`, `_db_path()`,
`_cache_file()`), so they are called directly with no `/ "<filename>.db"` suffix; the two that
return a directory (`store_root()`, `scheduler_dir()`) get their filename appended.

Pass it through:

```python
    results = run_all_checks(
        installed_version=__version__,
        settings_path=settings_path,
        bundle_path=bundle_path,
        skills_source_dir=skills_source_dir,
        skills_target_dir=skills_target_dir,
        env=env_vars,
        skip_pypi=args.no_pypi,
        store_paths=store_paths,
    )
```

### Step 8: Add one CLI-level smoke test

```python
# append to tests/test_ccst_doctor.py

def test_doctor_output_includes_data_store_checks() -> None:
    result = _run("doctor", "--no-pypi")
    assert "data-store:" in result.stdout
```

### Step 9: Run the full doctor test file and the CLI manually

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: PASS (all tests)

Run: `uv run python -m cc_session_tools.cli.ccst doctor --no-pypi`
Expected: output includes six `[OK  ] data-store:<name>` or `[WARN] data-store:<name>` lines (OK
if you've already used each subsystem's CLI at least once on this machine since Phases 2-6
landed; WARN for any not yet touched — neither should be FAIL on a healthy machine).

### Step 10: Commit

```bash
git add src/cc_session_tools/lib/doctor.py src/cc_session_tools/cli/ccst.py tests/test_ccst_doctor.py
git commit -m "feat(doctor): add check_data_stores — verify all six data stores can be opened"
```

---

## Task 2: `ccst install-everything` — confirm no new step is needed

**Files:** none modified (verification-only task)

The `_INSTALL_STEPS`/`_cmd_install_everything()` pipeline (`ccst.py:790-865`) is: skills → hooks →
shell → claude-md → doctor health-check (`5/5 Health check`, which calls `_cmd_doctor()`). Since
Task 1 just wired `check_data_stores()` into `_cmd_doctor()`, this final step now also surfaces
any store-open problem — no dedicated new pipeline stage is needed, because every store creates
its own schema lazily on first `connect(path, ddl=...)` call (design-spec §8.3 / overview binding
decision 8), so there's nothing to "provision" ahead of time the way `ccst skills install` has to
provision symlinks or `ccst hooks install` has to provision `settings.json` entries.

**Exception check:** the one plausible reason a store would need pre-population rather than
lazy creation is if some later consumer reads it *before* ever writing to it and treats "table
exists but empty" differently from "file doesn't exist at all" (e.g. a `sessions.db` reader that
errors on a missing table rather than returning zero rows). Confirm this isn't the case for any
of the six stores before closing this task:

### Step 1: Confirm every store module accepts a not-yet-existing directory gracefully

```bash
grep -n "def connect\|ddl=" src/cc_session_tools/lib/scheduler/store.py src/cc_session_tools/lib/messaging/store.py
grep -rln "ddl=" src/cc_session_tools/lib/sessions_db.py src/cc_session_tools/lib/telemetry_store.py 2>/dev/null
```

Every store module's read path should call `db.connect(path, ddl=<its own DDL>)` even for reads
(Phase 1's `connect()` runs `ddl` idempotently — see `test_connect_runs_ddl_idempotently` in
`tests/test_db.py`), so a first-ever read on a machine with no prior writes still gets a valid
empty schema rather than erroring. If any store's read path instead calls
`connect(path, readonly=True)` without ever having called the writing path first, running `ccmsg
list` (or the equivalent) on a brand-new machine before any `ccmsg send` would FAIL, not
gracefully return zero rows — if you find this, that store needs a small fix inside its own
phase's module (not a new Phase 7 install step); flag it as an exception, name the exact store
and file, and add a one-line fix task here scoped to defaulting that read path to
`ddl=<schema>` instead of `readonly=True` on a missing file.

### Step 2: Run `ccst install-everything` (dry run) and confirm the health check output

```bash
uv run python -m cc_session_tools.cli.ccst install-everything
```

Expected: five numbered sections print, the last being `=== 5/5  Health check ===`, followed by
`ccst doctor`'s full table — including the six `data-store:*` lines from Task 1. No crash, no
step needs `--apply` to demonstrate this (dry run is sufficient to prove the wiring).

### Step 3: No commit — this task changes no files (unless Step 1 found an exception)

If Step 1 found a genuine exception, implement the minimal fix inside the relevant phase's own
module (not here), write a regression test in that module's existing test file, and commit with a
message naming the store, e.g. `fix(sessions-db): ensure default_db_path() reads create schema on
first access`. Otherwise, record in the plan execution notes that no exception was found and move
to Task 3.

---

## Task 3: `ccst gc report` — extend to DB-backed stores

**Files:**
- Modify: `src/cc_session_tools/lib/session_gc.py`
- Modify: `src/cc_session_tools/cli/ccst.py:749-759` (`_cmd_gc_report`), `:1138-1173` (arg parser)
- Modify: `tests/test_ccst_gc_report.py`

Today `session_gc.py` reports orphans across four flat-file, per-session-uuid stores:
`scheduler-reconcile-markers` (`<scheduler_dir>/.reconcile.<uuid>.ts`), `scheduler-cursors`
(`<scheduler_dir>/.cursors/<uuid>.json`), `messages-cursors`
(`<messages_root>/.cursors/<uuid>.json`), and `session-env`
(`<session_env_dir>/<uuid>/`, harness-owned, untouched by this migration).

Once Phases 2-4 have landed (schemas below verified against the merged Phase 2/3/4 source, not the
design-spec intent — an earlier draft of this plan assumed a single combined scheduler table and a
uuid column on `sessions`, neither of which the phases actually built):
- The scheduler's reconcile-throttle marker and catch-up cursor move into `ccsched.db` as **two
  separate tables**, not one combined table: `cursors(session_uuid PRIMARY KEY, offset)` and
  `reconcile_throttle(session_uuid PRIMARY KEY, last_reconciled_at)` (Phase 3, `lib/scheduler/
  store.py`'s `_DDL`). Each table's row presence *is* the dimension — no per-column `IS NOT NULL`
  filtering is needed (that was an artefact of the assumed combined table).
- The messaging cursor moves into `ccmsg.db`'s `cursors` table, which is **composite-keyed**
  `(session_uuid, partition)` with a `high_water_message_id` column (Phase 2) — one session can
  have **N rows**, one per partition, so the orphan extractor must dedupe to distinct
  `session_uuid` (`SELECT DISTINCT`).
- `sessions.db` is a wholly new store that didn't exist in the original four. Its uuids live in the
  **`session_tags(uuid PRIMARY KEY, tag, updated_at)`** table (Phase 4) — **not** the `sessions`
  table, which is keyed by `(project_dir, basename)` and has **no uuid column** at all. The
  orphan extractor queries `session_tags.uuid`. A `session_tags` row can become orphaned if its
  session's transcript is deleted while the row survives, so it belongs in this report.
- `session-env` is unchanged (harness-owned, outside this repo's migration).

`StoreReport`, `GcReport`, and `format_report()` are confirmed store-agnostic by inspection
(`_store_report()` only reads `len(entries)` and iterates `entries`' keys; `format_report()` only
reads `.name`/`.total`/`.orphaned`) — **verify this is still true against the live file before
starting**, since Phases 1-6 may have touched `session_gc.py` for an unrelated reason. If it's
still true, no changes to those three are needed.

### Step 1: Read the live schema for each DB-backed store

```bash
# Point at a real or test .db file for each subsystem (or use `sqlite3 :memory:` against the
# CREATE TABLE strings found by grep, if no live file exists yet on this machine):
grep -n "CREATE TABLE" -A8 src/cc_session_tools/lib/scheduler/state.py src/cc_session_tools/lib/scheduler/*.py 2>/dev/null
grep -n "CREATE TABLE" -A8 src/cc_session_tools/lib/messaging/store.py src/cc_session_tools/lib/messaging/*.py 2>/dev/null
grep -n "CREATE TABLE" -A8 src/cc_session_tools/lib/sessions_db.py 2>/dev/null
```

The schema below is the **real, verified** Phase 2/3/4 layout (confirmed against the merged source
tree — the SQL in Step 3 and the seed-fixture DDL in Step 2 use exactly these names):

| gc dimension | DB file | Table | UUID column | Orphan query |
|---|---|---|---|---|
| `scheduler-reconcile-markers` | `ccsched.db` | `reconcile_throttle` | `session_uuid` | `SELECT session_uuid FROM reconcile_throttle` (row presence = has a marker) |
| `scheduler-cursors` | `ccsched.db` | `cursors` | `session_uuid` | `SELECT session_uuid FROM cursors` (row presence = has a cursor) |
| `messages-cursors` | `ccmsg.db` | `cursors` (composite PK `(session_uuid, partition)`) | `session_uuid` | `SELECT DISTINCT session_uuid FROM cursors` (N rows per session — one per partition) |
| `sessions-index` | `sessions.db` | `session_tags` | `uuid` | `SELECT uuid FROM session_tags` (the `sessions` table has no uuid column — it is keyed by `(project_dir, basename)`) |

Note the two same-named-but-distinct `cursors` tables live in different `.db` files (`ccsched.db`
vs `ccmsg.db`) with different schemas — no conflict, but keep the SQL constants distinct. The
dict-shape contract each extractor returns (`dict[str, Path]`, keyed by uuid) is unchanged.

### Step 2: Write the failing tests

Replace the module-level fixtures and the four flat-file-seeding helpers
(`_make_reconcile_marker`, `_make_cursor`) in `tests/test_ccst_gc_report.py` with DB-seeding
equivalents (`_make_session_env` stays unchanged — that store isn't migrating):

```python
# tests/test_ccst_gc_report.py — replace _make_reconcile_marker and _make_cursor with:
from cc_session_tools.lib import db as _db

# ccsched.db: TWO separate tables (Phase 3 lib/scheduler/store.py _DDL) — NOT one combined table.
_SCHEDULER_DDL = """
CREATE TABLE IF NOT EXISTS cursors (
    session_uuid TEXT PRIMARY KEY,
    offset       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reconcile_throttle (
    session_uuid       TEXT PRIMARY KEY,
    last_reconciled_at TEXT NOT NULL
);
"""
# ccmsg.db: composite-keyed cursors, one row per (session_uuid, partition) (Phase 2).
_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS cursors (
    session_uuid          TEXT NOT NULL,
    partition             TEXT NOT NULL,
    high_water_message_id TEXT NOT NULL,
    PRIMARY KEY (session_uuid, partition)
);
"""
# sessions.db: uuids live in session_tags, NOT in the (project_dir, basename)-keyed sessions table (Phase 4).
_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS session_tags (
    uuid       TEXT PRIMARY KEY,
    tag        TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _seed_scheduler_row(
    scheduler_dir: Path, uuid: str, *, reconcile: bool = False, cursor: bool = False
) -> None:
    conn = _db.connect(scheduler_dir / "ccsched.db", ddl=_SCHEDULER_DDL)
    if reconcile:
        conn.execute(
            "INSERT OR REPLACE INTO reconcile_throttle (session_uuid, last_reconciled_at) "
            "VALUES (?, ?)",
            (uuid, "2026-07-01T00:00:00Z"),
        )
    if cursor:
        conn.execute(
            "INSERT OR REPLACE INTO cursors (session_uuid, offset) VALUES (?, ?)", (uuid, 42)
        )
    conn.commit()
    conn.close()


def _seed_messages_cursor(messages_root: Path, uuid: str, *, partition: str = "projects/alpha") -> None:
    conn = _db.connect(messages_root / "ccmsg.db", ddl=_MESSAGES_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO cursors (session_uuid, partition, high_water_message_id) "
        "VALUES (?, ?, ?)",
        (uuid, partition, "20260701T000000Z-0001"),
    )
    conn.commit()
    conn.close()


def _seed_sessions_row(sessions_dir: Path, uuid: str) -> None:
    conn = _db.connect(sessions_dir / "sessions.db", ddl=_SESSIONS_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO session_tags (uuid, tag, updated_at) VALUES (?, ?, ?)",
        (uuid, "t", "2026-07-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()
```

Because `ccmsg.db`'s `cursors` is one-row-per-partition, add a test proving the messages extractor
dedupes a multi-partition session to a single uuid (so `store.total` counts distinct uuids, not raw
rows):

```python
def test_messages_cursor_multi_partition_session_counts_once(gc_dirs: dict[str, Path]) -> None:
    _make_transcript(gc_dirs["projects_dir"], LIVE_UUID)
    _seed_messages_cursor(gc_dirs["messages_root"], LIVE_UUID, partition="projects/alpha")
    _seed_messages_cursor(gc_dirs["messages_root"], LIVE_UUID, partition="projects/beta")

    report = build_report(**gc_dirs)

    messages_store = next(s for s in report.stores if s.name == "messages-cursors")
    assert messages_store.total == 1     # two cursor rows, one distinct session uuid
    assert messages_store.orphaned == 0
```

Update the `gc_dirs` fixture to add a `sessions_dir` key, and rewrite every test that called
`_make_reconcile_marker`/`_make_cursor` to call the new seed helpers instead — e.g.:

```python
@pytest.fixture
def gc_dirs(tmp_path: Path) -> dict[str, Path]:
    return {
        "projects_dir": tmp_path / "projects",
        "scheduler_dir": tmp_path / "cc-scheduler",
        "messages_root": tmp_path / "cc-messages",
        "session_env_dir": tmp_path / "session-env",
        "sessions_dir": tmp_path / "sessions",
    }


def test_live_uuid_not_orphaned_in_any_store(gc_dirs: dict[str, Path]) -> None:
    _make_transcript(gc_dirs["projects_dir"], LIVE_UUID)
    _seed_scheduler_row(gc_dirs["scheduler_dir"], LIVE_UUID, reconcile=True, cursor=True)
    _seed_messages_cursor(gc_dirs["messages_root"], LIVE_UUID)
    _make_session_env(gc_dirs["session_env_dir"], LIVE_UUID)
    _seed_sessions_row(gc_dirs["sessions_dir"], LIVE_UUID)

    report = build_report(**gc_dirs)

    assert report.known_uuid_count == 1
    for store in report.stores:
        assert store.total == 1
        assert store.orphaned == 0
        assert LIVE_UUID not in store.orphaned_uuids


def test_orphan_uuid_reported_in_every_store(gc_dirs: dict[str, Path]) -> None:
    _seed_scheduler_row(gc_dirs["scheduler_dir"], ORPHAN_UUID, reconcile=True, cursor=True)
    _seed_messages_cursor(gc_dirs["messages_root"], ORPHAN_UUID)
    _make_session_env(gc_dirs["session_env_dir"], ORPHAN_UUID)
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID)

    report = build_report(**gc_dirs)

    assert report.known_uuid_count == 0
    for store in report.stores:
        assert store.total == 1
        assert store.orphaned == 1
        assert ORPHAN_UUID in store.orphaned_uuids


def test_mixed_live_and_orphan_uuids(gc_dirs: dict[str, Path]) -> None:
    _make_transcript(gc_dirs["projects_dir"], LIVE_UUID)
    _seed_scheduler_row(gc_dirs["scheduler_dir"], LIVE_UUID, reconcile=True)
    _seed_scheduler_row(gc_dirs["scheduler_dir"], ORPHAN_UUID, reconcile=True)

    report = build_report(**gc_dirs)

    reconcile_store = next(s for s in report.stores if s.name == "scheduler-reconcile-markers")
    assert reconcile_store.total == 2
    assert reconcile_store.orphaned == 1
    assert reconcile_store.orphaned_uuids == (ORPHAN_UUID,)


def test_scheduler_cursor_and_reconcile_are_independent_dimensions(gc_dirs: dict[str, Path]) -> None:
    """A row with only a cursor set (no reconcile throttle) counts for
    scheduler-cursors but not scheduler-reconcile-markers, and vice versa."""
    _seed_scheduler_row(gc_dirs["scheduler_dir"], LIVE_UUID, cursor=True)  # no reconcile

    report = build_report(**gc_dirs)

    reconcile_store = next(s for s in report.stores if s.name == "scheduler-reconcile-markers")
    cursor_store = next(s for s in report.stores if s.name == "scheduler-cursors")
    assert reconcile_store.total == 0
    assert cursor_store.total == 1


def test_new_sessions_index_store_reports_orphans(gc_dirs: dict[str, Path]) -> None:
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID)

    report = build_report(**gc_dirs)

    sessions_store = next(s for s in report.stores if s.name == "sessions-index")
    assert sessions_store.total == 1
    assert sessions_store.orphaned == 1
```

Update the remaining pre-existing tests in the file the same way (`test_build_report_all_stores_missing_returns_zeroes`,
`test_format_report_includes_store_names_and_counts`, `test_build_report_never_deletes_or_modifies_files`,
the two CLI-integration tests, `test_gc_report_type_is_gcreport`) — swap flat-file seed calls for
DB seed calls and add `sessions_dir` to every `gc_dirs` reference and every `--sessions-dir` CLI
invocation (Step 4 below adds that flag). `test_session_env_ignores_non_directory_entries` and
`test_empty_session_env_dir_for_orphan_uuid_is_counted` need no change — `session-env` isn't
migrating.

### Step 3: Run tests to verify they fail

Run: `uv run pytest tests/test_ccst_gc_report.py -v`
Expected: FAIL — `build_report()` doesn't accept `sessions_dir` yet, and the DB-backed extractors
don't exist yet.

### Step 4: Implement the DB-backed extractors and extend `build_report()`

```python
# src/cc_session_tools/lib/session_gc.py — replace the module's existing
# _reconcile_marker_uuids() and _cursor_uuids() functions, and their two
# call sites for scheduler/messages, with:

from cc_session_tools.lib import db as _db

# Verified table/uuid-column names (Phase 2/3/4 merged source — see Task 3 Step 1's table).
# ccsched.db keeps reconcile-throttle and catch-up-cursor as TWO SEPARATE tables.
_SCHEDULER_CURSORS_TABLE = "cursors"            # ccsched.db (Phase 3)
_SCHEDULER_RECONCILE_TABLE = "reconcile_throttle"  # ccsched.db (Phase 3)
_MESSAGES_CURSOR_TABLE = "cursors"              # ccmsg.db, composite PK (session_uuid, partition) (Phase 2)
_SESSION_TAGS_TABLE = "session_tags"            # sessions.db, uuid-keyed (Phase 4)


def _scheduler_cursor_uuids_db(ccsched_db_path: Path) -> dict[str, Path]:
    """Session uuids with a catch-up-cursor row in ccsched.db (Phase 3's
    `cursors` table — row presence is the dimension)."""
    if not ccsched_db_path.exists():
        return {}
    conn = _db.connect(ccsched_db_path, readonly=True)
    try:
        rows = conn.execute(
            f"SELECT session_uuid FROM {_SCHEDULER_CURSORS_TABLE}"
        ).fetchall()
    finally:
        conn.close()
    return {row["session_uuid"]: ccsched_db_path for row in rows}


def _scheduler_reconcile_uuids_db(ccsched_db_path: Path) -> dict[str, Path]:
    """Session uuids with a reconcile-throttle row in ccsched.db (Phase 3's
    `reconcile_throttle` table — a table kept SEPARATE from `cursors`, so this
    is an independent dimension: a session can have a cursor but no throttle
    marker, and vice versa)."""
    if not ccsched_db_path.exists():
        return {}
    conn = _db.connect(ccsched_db_path, readonly=True)
    try:
        rows = conn.execute(
            f"SELECT session_uuid FROM {_SCHEDULER_RECONCILE_TABLE}"
        ).fetchall()
    finally:
        conn.close()
    return {row["session_uuid"]: ccsched_db_path for row in rows}


def _messages_cursor_uuids_db(ccmsg_db_path: Path) -> dict[str, Path]:
    """Distinct session uuids with a cursor row in ccmsg.db. The `cursors`
    table is composite-keyed `(session_uuid, partition)` (Phase 2), so one
    session yields N rows (one per partition); SELECT DISTINCT collapses them
    to one entry so `store.total` counts distinct sessions, not raw rows."""
    if not ccmsg_db_path.exists():
        return {}
    conn = _db.connect(ccmsg_db_path, readonly=True)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT session_uuid FROM {_MESSAGES_CURSOR_TABLE}"
        ).fetchall()
    finally:
        conn.close()
    return {row["session_uuid"]: ccmsg_db_path for row in rows}


def _sessions_db_uuids(sessions_db_path: Path) -> dict[str, Path]:
    """Session uuids with a row in sessions.db's `session_tags` table (Phase 4).
    NOT the `sessions` table — that is keyed by `(project_dir, basename)` and
    has no uuid column; uuids live only in `session_tags(uuid, tag, updated_at)`.
    Note the column is `uuid`, not `session_uuid`."""
    if not sessions_db_path.exists():
        return {}
    conn = _db.connect(sessions_db_path, readonly=True)
    try:
        rows = conn.execute(f"SELECT uuid FROM {_SESSION_TAGS_TABLE}").fetchall()
    finally:
        conn.close()
    return {row["uuid"]: sessions_db_path for row in rows}
```

`_session_env_uuids()` is unchanged (harness-owned flat directory, not part of this migration).

Update the imports at the top of the module: add the Phase 4 accessor `default_db_path` (verified
in Task 1 Step 1 — it returns the full `sessions.db` path, honouring `CCST_SESSIONS_DIR`):

```python
from cc_session_tools.lib.sessions_db import default_db_path as _default_sessions_db_path
```

Update `build_report()`:

```python
def build_report(
    *,
    projects_dir: Path | None = None,
    scheduler_dir: Path | None = None,
    messages_root: Path | None = None,
    session_env_dir: Path | None = None,
    sessions_dir: Path | None = None,
) -> GcReport:
    projects_dir = projects_dir if projects_dir is not None else DEFAULT_PROJECTS_DIR
    scheduler_dir = scheduler_dir if scheduler_dir is not None else _default_scheduler_dir()
    messages_root = messages_root if messages_root is not None else _default_messages_root()
    session_env_dir = (
        session_env_dir if session_env_dir is not None else DEFAULT_SESSION_ENV_DIR
    )
    # default_db_path() returns the full sessions.db path; take its parent so
    # `sessions_dir` stays a directory, consistent with the --sessions-dir CLI flag.
    sessions_dir = sessions_dir if sessions_dir is not None else _default_sessions_db_path().parent

    known = known_session_uuids(projects_dir)

    stores = (
        _store_report(
            "scheduler-reconcile-markers",
            _scheduler_reconcile_uuids_db(scheduler_dir / "ccsched.db"),
            known,
        ),
        _store_report(
            "scheduler-cursors",
            _scheduler_cursor_uuids_db(scheduler_dir / "ccsched.db"),
            known,
        ),
        _store_report(
            "messages-cursors",
            _messages_cursor_uuids_db(messages_root / "ccmsg.db"),
            known,
        ),
        _store_report(
            "session-env",
            _session_env_uuids(session_env_dir),
            known,
        ),
        _store_report(
            "sessions-index",
            _sessions_db_uuids(sessions_dir / "sessions.db"),
            known,
        ),
    )
    return GcReport(known_uuid_count=len(known), stores=stores)
```

Update the module's top-of-file docstring: it currently describes the four *flat-file* stores by
their old paths (`.reconcile.<uuid>.ts`, `.cursors/<uuid>.json` x2, `session-env/<uuid>/`) — replace
the first three with their new `.db` table descriptions, keep `session-env` as-is, and add the new
`sessions.db` row to the list.

Also flag, as a comment near `known_session_uuids()` (unchanged in this task — out of scope to
fix here, per this plan's brief), that it remains a `projects_dir.glob("*/*.jsonl")` filesystem
walk run fresh on every `gc report` invocation, and note it as a follow-up candidate once
`sessions.db` is authoritative enough to answer "which session uuids are known" without a
directory walk:

```python
def known_session_uuids(projects_dir: Path) -> set[str]:
    """Return every session uuid with a transcript under
    ``<projects_dir>/*/<uuid>.jsonl``.

    Still a filesystem walk, not a sessions.db query, even though sessions.db
    (Phase 4) now indexes most of the same uuids — left as a directory walk
    in Phase 7 deliberately (out of scope to change here); a good follow-up
    candidate once sessions.db is confirmed authoritative for "which session
    uuids exist," since a transcript being deleted by hand would otherwise
    silently desync the two.
    """
```

### Step 5: Run tests to verify they pass

Run: `uv run pytest tests/test_ccst_gc_report.py -v`
Expected: PASS (all tests)

### Step 6: Wire the CLI

```python
# src/cc_session_tools/cli/ccst.py — modify _cmd_gc_report (line ~749)
def _cmd_gc_report(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.session_gc import build_report, format_report

    report = build_report(
        projects_dir=Path(args.projects_dir) if args.projects_dir else None,
        scheduler_dir=Path(args.scheduler_dir) if args.scheduler_dir else None,
        messages_root=Path(args.messages_root) if args.messages_root else None,
        session_env_dir=Path(args.session_env_dir) if args.session_env_dir else None,
        sessions_dir=Path(args.sessions_dir) if args.sessions_dir else None,
    )
    print(format_report(report))
    return 0
```

```python
# src/cc_session_tools/cli/ccst.py — modify the gc report arg parser (line ~1142-1173)
    gc_report_parser = gc_sub.add_parser(
        "report",
        help=(
            "Report orphaned per-session-uuid entries across the scheduler, "
            "messaging, session-env, and sessions-index stores. Report-only "
            "— never deletes or modifies anything."
        ),
    )
    gc_report_parser.add_argument(
        "--projects-dir",
        default=None,
        metavar="PATH",
        help="Transcript projects directory (default: ~/.claude/projects/)",
    )
    gc_report_parser.add_argument(
        "--scheduler-dir",
        default=None,
        metavar="PATH",
        help="Scheduler directory holding ccsched.db (default: from CC_SCHEDULER_DIR or data_home())",
    )
    gc_report_parser.add_argument(
        "--messages-root",
        default=None,
        metavar="PATH",
        help="Messaging store directory holding ccmsg.db (default: from CCST_MESSAGES_ROOT or data_home())",
    )
    gc_report_parser.add_argument(
        "--session-env-dir",
        default=None,
        metavar="PATH",
        help="Session-env directory (default: ~/.claude/session-env/)",
    )
    gc_report_parser.add_argument(
        "--sessions-dir",
        default=None,
        metavar="PATH",
        help="Directory holding sessions.db (default: from CCST_SESSIONS_DIR or data_home())",
    )
```

Update the two `--scheduler-dir`/`--messages-root` help strings' wording (shown above) to reflect
that they now point at a directory containing a `.db` file rather than a directory of flat files.

### Step 7: Add CLI-level test for the new flag

```python
# append to tests/test_ccst_gc_report.py

def test_cli_gc_report_accepts_sessions_dir_flag(gc_dirs: dict[str, Path]) -> None:
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID)

    result = _run(
        "gc", "report",
        "--projects-dir", str(gc_dirs["projects_dir"]),
        "--scheduler-dir", str(gc_dirs["scheduler_dir"]),
        "--messages-root", str(gc_dirs["messages_root"]),
        "--session-env-dir", str(gc_dirs["session_env_dir"]),
        "--sessions-dir", str(gc_dirs["sessions_dir"]),
    )
    assert result.returncode == 0
    assert "sessions-index" in result.stdout
```

Also add `"--sessions-dir", str(gc_dirs["sessions_dir"])` to the existing CLI-integration tests'
`_run(...)` calls (`test_cli_gc_report_never_deletes_or_modifies_files`,
`test_cli_gc_report_exits_ok_with_no_stores`, `test_cli_gc_report_reports_orphan_counts`), and add
`"sessions-index"` to the assertion in `test_cli_gc_report_reports_orphan_counts`.

### Step 8: Run the full gc-report test file

Run: `uv run pytest tests/test_ccst_gc_report.py -v`
Expected: PASS (all tests, including the new ones)

### Step 9: Run `ccst gc report` manually

```bash
uv run python -m cc_session_tools.cli.ccst gc report
```

Expected: five rows in the output table — `scheduler-reconcile-markers`, `scheduler-cursors`,
`messages-cursors`, `session-env`, `sessions-index` — with real counts if any of the corresponding
DB files exist on this machine already.

### Step 10: Commit

```bash
git add src/cc_session_tools/lib/session_gc.py src/cc_session_tools/cli/ccst.py tests/test_ccst_gc_report.py
git commit -m "feat(gc-report): switch scheduler/messages extractors to DB-backed, add sessions-index store"
```

---

## Task 4: Tar-backup safety net + old-flat-file retirement checklist

**Files:**
- Create: `docs/data-store-migration-backups.md`

Per the overview's binding decision 4, every migration script in Phases 2-5 already takes its own
`tar czf` backup of the pre-migration flat-file tree before deleting old files, as part of that
phase's own plan — this task is not automated pruning (explicitly out of scope per the source
spec's §10) and does not re-implement any of that. It produces one consolidated, human-auditable
checklist: a place to confirm all four migrations actually ran and verified successfully on the
machine doing the cutover, and a stated retention window for the resulting backup files.

### Step 1: Write the checklist document

```markdown
# Data-store migration backups — audit checklist

> Manual checklist, not automated. Each of Phases 2, 3, 4, and 5 (the phases with pre-existing
> flat-file data) takes its own `tar czf` backup of the pre-migration tree before deleting old
> files, per `2026-07-13-data-store-uplift-00-overview.md`'s binding decision 4. This document is
> how a human confirms, after the fact, that every migration actually ran and verified — not a
> substitute for reading each phase's own migration-script output at the time it ran.

## Expected backup files

All under `~/.local/share/claude/migration-backups/`:

| Phase | Subsystem | Expected filename pattern | Source tree backed up |
|---|---|---|---|
| 2 | `ccmsg` | `ccmsg-<date>.tar.gz` | `~/.claude/cc-messages/` |
| 3 | `ccsched` | `ccsched-<date>.tar.gz` | `~/.claude/cc-scheduler/` (`jobs.toml`, `state.json`, `.cursors/`, `.reconcile.*.ts`) |
| 4 | `sessions.db` | `sessions-<date>.tar.gz` | `~/.cache/claude/session-tags/*.tag`, `~/.claude/projects/**/.last-active`, `.last-opened` |
| 5 | `telemetry.db` | `telemetry-<date>.tar.gz` | `~/.cache/claude/logs/fires.jsonl` and rotated slots |

Phase 6 (`command-cache.db`, `claude-flags.json`) is a path move only — no data transformation, no
backup script, per §8.1 of the design spec ("path move only").

## Audit steps (run once, after all of Phases 2-6 have landed and been used at least once)

- [ ] `ls -la ~/.local/share/claude/migration-backups/` — confirm all four files above exist.
- [ ] For each, confirm the corresponding phase's own migration script printed a verification
      success message when it ran (row counts matched, spot-check passed) — check that phase's own
      terminal output/log if still available, or re-run the migration script's `--verify-only`
      mode (if it has one) against the new `.db` file and the still-present old flat files.
- [ ] Confirm the old flat-file trees listed in the table above are gone (each migration script
      only deletes them after its own backup+verify steps pass) — if any are still present, the
      corresponding migration did not complete; do not delete this checklist's backups until it
      has.

## Retention window

**Keep for 30 days after the audit above passes, then safe to delete manually.**

30 days is long enough to catch a subtle post-migration correctness bug in real day-to-day use
(a message or job whose old-format edge case the migration script's row-count/spot-check pass
missed) while the pre-migration data is still one `tar xzf` away, without accumulating
multi-gigabyte archives indefinitely. This is a manual step, not an automated retention policy —
automated pruning of these backups is explicitly out of scope for this migration (source spec
§10).

```bash
# after the retention window and the audit above both pass:
rm ~/.local/share/claude/migration-backups/*.tar.gz
```
```

### Step 2: Verify the file reads correctly

```bash
git diff --stat docs/data-store-migration-backups.md
```

Expected: new file, no other changes.

### Step 3: Commit

```bash
git add docs/data-store-migration-backups.md
git commit -m "docs: add data-store migration backups audit checklist and retention window"
```

---

## Task 5: Docs, CHANGELOG, version bump

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-06-20-inter-session-messaging.md`
- Modify: `.claude/CLAUDE.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`

No automated tests for this task — each step is followed by a `git diff` review instead. The
exact line numbers and surrounding wording below are current as of this investigation; re-read
each live file before editing, since Phases 2-6 landing may have already touched nearby prose.

### Step 1: Update `README.md`'s stale path references

Read the live file first:

```bash
grep -n "cc-messages\|cc-scheduler\|jobs\.toml\|state\.json\|session-tags\|cc-doctor-mutes\|command-cache\.db\|claude-flags\.json\|fires\.jsonl" README.md
```

As of this investigation the hits needing an update, and their replacement, are:

| Line | Old | New |
|---|---|---|
| 53 | `Sweeps \`~/.claude/cc-messages/\` for messages addressed to this session...` | `Sweeps \`ccmsg.db\` (under \`~/.local/share/claude/\`, overridable via \`CCST_MESSAGES_ROOT\`) for messages addressed to this session...` |
| 415 | `Messages are stored as markdown-with-frontmatter files under \`~/.claude/cc-messages/\` — on-disk, human-readable, and auditable.` | `Messages are stored as rows (the body itself as a TEXT column) in \`ccmsg.db\`, a SQLite (WAL mode) database under \`~/.local/share/claude/\` — overridable via \`CCST_MESSAGES_ROOT\` — auditable via \`ccmsg list\`/\`ccmsg read\` or any SQLite client.` |
| 451 | `\`ccsched\` registers local recurring jobs in \`~/.claude/cc-scheduler/jobs.toml\` and reconciles them...` | `\`ccsched\` registers local recurring jobs in \`ccsched.db\` (SQLite, WAL mode, under \`~/.local/share/claude/\`, overridable via \`CC_SCHEDULER_DIR\`) and reconciles them...` |
| 508 | `...every action is recorded to the shared \`~/.cache/claude/logs/fires.jsonl\` telemetry ledger.` | `...every action is recorded to the shared \`telemetry.db\` (SQLite, under \`~/.local/share/claude/\`, overridable via \`CCCS_HOOKS_DIR\`) telemetry ledger — query it with \`ccst telemetry query\`.` |
| 523 | `The registry lives at \`~/.claude/cc-scheduler/jobs.toml\` — plain TOML, hand-editable, created lazily on first \`ccsched add\`.` | `The registry lives in \`ccsched.db\` — created lazily on first \`ccsched add\`; inspect it with \`ccsched list\`/\`ccsched status\` or any SQLite client (no longer hand-editable TOML).` |
| 539 | `Writes structured JSONL to \`~/.cache/claude/logs/fires.jsonl\`; used by other modules. Rotates at 10 MB (numbered slots: \`fires.jsonl.1\`, \`.2\`, \`.3\`).` | `Writes rows to \`telemetry.db\` (SQLite, WAL mode, under \`~/.local/share/claude/\`); used by other modules. Query via \`ccst telemetry query\`.` |
| 550 | `Sweeps \`~/.claude/cc-messages/\` for messages addressed to this session and injects a compact digest...` | `Sweeps \`ccmsg.db\` for messages addressed to this session and injects a compact digest...` |
| 585 | `File written: \`~/.cache/claude/session-tags/<session_id>.tag\` (flat layout keyed by UUID; overrideable via \`CCCS_SESSION_TAGS_DIR\`)` | `Row written: a \`sessions\` table entry keyed by session UUID in \`sessions.db\` (SQLite, under \`~/.local/share/claude/\`, overridable via \`CCST_SESSIONS_DIR\`)` — **before committing this one, read \`src/cccs_hooks/session_tag.py\` as it stands after Phase 4 landed and confirm the hook really does write a \`sessions.db\` row now rather than still writing a \`.tag\` file; adjust the wording if Phase 4 kept a flat file for this specific hook.** |
| 798 | `Prune old hook telemetry data from \`~/.cache/claude/logs/fires.jsonl\`.` | `Prune old hook telemetry rows from \`telemetry.db\` (SQLite, under \`~/.local/share/claude/\`).` |
| 805 | `Telemetry is also rotated automatically at 10 MB (numbered slots \`fires.jsonl.1/.2/.3\`).` | Delete this sentence — byte-size file rotation doesn't apply to a SQLite table. **Before deleting, confirm against Phase 5's actual final implementation** whether some other SQLite-appropriate maintenance note belongs here instead (e.g. a periodic `VACUUM`/checkpoint via the existing `ccsched`-driven `telemetry-trim-weekly` job) — if so, replace the sentence with accurate wording rather than deleting outright. |

Lines 49, 544-545, and 737 (skill-marker `~/.cache/claude/markers/` references) are **not** part
of this migration — they're the unrelated skill-marker TTL mechanism (already fixed by an earlier
commit per this repo's own git log, `a4cb242`). Confirm they're still describing
`~/.cache/claude/markers/` correctly and leave them untouched.

`command-cache.db` and `claude-flags.json` have no README hits as of this investigation (re-confirm
with the grep above) — no README change needed for Phase 6's relocations.

### Step 2: Review the README diff

```bash
git diff README.md
```

Expected: only the lines identified above changed; no unrelated reflow.

### Step 3: Add a forward-pointer to the historical inter-session-messaging plan doc

Read `docs/superpowers/plans/2026-06-20-inter-session-messaging.md:203-206` (and skim the rest of
that doc for any other now-stale path description — grep it the same way as Step 1). This is a
historical plan document recording a decision already implemented and merged; rather than editing
its prose in place (which would misrepresent what was actually decided/built at the time), add a
one-line forward-pointer note directly above the stale section:

```markdown
> **Update (2026-07-13):** the message store described below has since moved to `ccmsg.db`
> (SQLite) under `~/.local/share/claude/` — see
> `2026-07-13-data-store-uplift-00-overview.md` and `2026-07-13-data-store-uplift-02-ccmsg.md`
> for the current layout. This section is left as-written for historical accuracy.
```

This choice (forward-pointer over in-place edit) is because a plan document's job is to record
what was decided and built *at the time*, not to stay perpetually current — editing historical
plans to match every later change makes them unreliable as a record of what actually happened at
each point in time. A one-line pointer costs nothing and saves a future reader from following
stale instructions.

### Step 4: Add a local-discoverability pointer to `.claude/CLAUDE.md`

Read the live file first — it already has an uncommitted "Data store conventions" section per
current git status. Add one sentence to its existing "Rationale and full design" paragraph:

```markdown
Rationale and full design: `data-stores-design-spec.md` and
`ccst-migration-and-cli-update-spec.md`, in the `claude` project's session
`cc-sessions/20260712-claude-finalise-common-extra-claude-data-store-requirements/out/`. This
repo's own local migration plan and phase index:
`docs/superpowers/plans/2026-07-13-data-store-uplift-00-overview.md`.
```

### Step 5: Cut a `CHANGELOG.md` section

Read `CHANGELOG.md:1-45` first to confirm the `## [Unreleased]` header is still present and empty
(confirmed at lines 8-10 as of this investigation) and to match the existing entry style (bold
lead-in per bullet, one bullet per notable change). Add:

```markdown
## [Unreleased]

### Changed

- **Data stores migrated to SQLite under a new root, `~/.local/share/claude/`.** Replaces the
  previous mix of `~/.claude/...`, `~/.cache/claude/...`, and `~/.cache/cc-session-tools/...`
  flat-file/TOML/JSONL stores with one WAL-mode SQLite `.db` file per subsystem, opened through a
  shared connection-setup helper (`lib/db.py`). External CLI interfaces (`ccmsg`/`ccsched`
  arguments and output shapes) are unchanged — only the storage backend moved. One-time migration
  scripts (run manually per machine, not part of `ccst install`) back up and verify before
  deleting any old flat files.
  - `ccmsg` → `ccmsg.db` (message store, cursors, and the message body itself as a TEXT column).
    Closes a pre-existing retention double-unlink race as a side effect (atomic `DELETE`/`UPDATE`
    replaces an unguarded `path.unlink()`).
  - `ccsched` → `ccsched.db` (job registry, run state, in-flight locks, catch-up cursor, and
    reconcile throttle). Replaces `state.json`'s full-file read-modify-write with targeted
    single-row `UPDATE`s.
  - `sessions.db` (new) — consolidates the session-tag cache and activity sentinels into one
    indexed table; `ccl --global`/`ccr` now query it instead of walking every encoded project
    directory. `~/.claude/cc-doctor-mutes.json` moves in as a table alongside it.
  - `telemetry.db` (new, replaces `fires.jsonl`) — new `ccst telemetry query` command; fixes a
    rotation/cursor-desync bug by replacing the byte-offset cursor with a monotonic row id.
  - `command-cache.db` and `claude-flags.json` relocate under the new root (path moves only); the
    latter's non-atomic write is also fixed.
  - `ccst doctor` gains a data-store health check (all six stores); `ccst gc report` gains a
    `sessions-index` store and switches its scheduler/messaging orphan detection from directory
    walks to indexed `.db` queries.
```

### Step 6: Review the CHANGELOG diff

```bash
git diff CHANGELOG.md
```

Expected: only the new `### Changed` block under the existing `## [Unreleased]` header.

### Step 7: Bump the version

Read `pyproject.toml`'s current `version = "..."` line first — confirmed `"0.18.0"` as of this
investigation; re-check it hasn't moved since (e.g. a release cut mid-migration).

```python
# pyproject.toml
version = "0.19.0"
```

Minor bump (not patch), per this repo's own `.claude/CLAUDE.md` version policy: this migration
changes CLI surface (`ccst telemetry query` is new, `ccst gc report` gains `--sessions-dir`) and
the storage/config contract (new env vars, new default paths).

### Step 8: Review the version-bump diff

```bash
git diff pyproject.toml
```

Expected: exactly one line changed.

### Step 9: Commit

```bash
git add README.md docs/superpowers/plans/2026-06-20-inter-session-messaging.md .claude/CLAUDE.md CHANGELOG.md pyproject.toml
git commit -m "docs: update stale data-store paths, cut CHANGELOG entry, bump version to 0.19.0"
```

---

## Full migration verification

Run once all five tasks above are complete, as the final gate before this phase (and the whole
migration) is considered done.

- [ ] **Full test suite passes**

```bash
uv run pytest -q
```

Expected: all tests pass, including every new test added across Tasks 1 and 3 and every test
carried over from Phases 1-6.

- [ ] **Linter/type-checker, if configured**

```bash
uv run ruff check src/cc_session_tools/lib/doctor.py src/cc_session_tools/lib/session_gc.py src/cc_session_tools/cli/ccst.py
uv run mypy src/cc_session_tools/lib/doctor.py src/cc_session_tools/lib/session_gc.py src/cc_session_tools/cli/ccst.py
```

(Check `pyproject.toml`/CI config first for the exact configured commands if these differ.)

- [ ] **`ccst doctor` — confirm all six data stores show OK or WARN, never FAIL**

```bash
uv run python -m cc_session_tools.cli.ccst doctor
```

- [ ] **`ccst gc report` — confirm five stores print, including the new `sessions-index`**

```bash
uv run python -m cc_session_tools.cli.ccst gc report
```

- [ ] **`ccst install-everything` — confirm the full pipeline runs clean**

```bash
uv run python -m cc_session_tools.cli.ccst install-everything --apply
```

- [ ] **Smoke-test each migrated CLI's real interface against a live (test) environment** — every
      command below should behave exactly as it did before the migration; only the storage
      backend changed:

```bash
# Messaging
ccmsg send --to <some-tag> --subject "phase 7 smoke test" --body "hello"
ccmsg list
ccmsg read <id-from-list>

# Scheduling
ccsched add --cadence every:1d --command "echo hi" --name phase7-smoke
ccsched list
ccsched run phase7-smoke

# Session listing / resume (sessions.db-backed)
ccl --global
ccr <some-tag>

# Telemetry
ccst telemetry query --limit 20
```

Expected: identical output shape to pre-migration behaviour in every case (per the overview's
hard constraint — CLI interfaces do not change). Any discrepancy here is a regression in whichever
phase owns that store, not something to patch inside Phase 7.

- [ ] **Final commit check**

```bash
git log --oneline -10
git status
```

Expected: one commit per task (5 total across Task 1, 3, 4, 5 — Task 2 has no commit unless it
found an exception), clean working tree, nothing uncommitted.

## Handoff

Phase 7, and the whole `f/claude-data-store-uplift` migration, is complete when: the full test
suite passes, `ccst doctor` and `ccst gc report` both cover all six/five stores respectively, the
manual smoke-test checklist above passes against a real environment, `README.md`/`.claude/CLAUDE.md`
no longer point at any pre-migration path, `CHANGELOG.md` has an `[Unreleased]` entry summarizing
the whole migration, and `pyproject.toml` is at `0.19.0`. At that point this branch is ready for
its own PR into `main`, per this repo's standard `superpowers:finishing-a-development-branch` flow.
