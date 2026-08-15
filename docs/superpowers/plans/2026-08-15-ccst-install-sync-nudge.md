# ccst install-sync nudge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** After `uv tool install --reinstall`/`--upgrade` (or `pip`/`pipx` equivalents) bumps the
installed `ccst` version, make it impossible for a human sitting at a terminal to run another
`ccst` command without being told `ccst install-everything --apply` hasn't been run for the new
version yet — without ever breaking an automated invocation (a Claude Code hook, a scheduled
`ccsched` job, or any other non-interactive caller) that happens to run on a stale install.

**Architecture:** A small persisted "last version `install-everything --apply` succeeded for"
marker, plus two consumers of it:

1. `ccst install-everything --apply` writes the marker (only when it and its steps all report
   success) to a new `install_sync` table in the existing `sessions.db` file — the established
   pattern for small persistent CLI state in this codebase (`doctor_mutes` already lives there for
   exactly this reason; see `src/cc_session_tools/lib/sessions_db.py`'s module docstring).
2. `ccst doctor` gains a new check comparing the installed version against the marker — WARN, not
   FAIL, since it's always self-recoverable with one command and doesn't indicate corrupted data
   (matching the existing `ccsched-job:*` checks' severity rationale).
3. `ccst`'s shared entrypoint (`main()`) gains a gate: **before dispatching any command**, if the
   installed version doesn't match the marker AND the invocation looks interactive AND the command
   isn't itself the fix (`install-everything`) or the diagnostic tool (`doctor`), print a short
   message and exit 1 instead of running the command.

**The "interactive" test is `sys.stderr.isatty()`, not a hand-maintained list of exempt
commands.** An earlier draft of this plan exempted only `ccst hooks run <verb>` by name — the
obvious hot path, invoked automatically on every Claude Code tool call. Investigating further
found a second, easy-to-miss automated path: `ccsched` already runs `ccst pdata
reconcile-session-output --all-projects` and `ccst pdata verify --all-projects` on unattended
daily/weekly schedules (`src/cc_session_tools/lib/scheduler/bundled_jobs.py`) — neither is `hooks
run`, so a noun/verb allowlist would have missed it, and `pdata-verify-all` auto-suspends after 10
consecutive failures, meaning a missed exemption wouldn't just be noisy, it would silently disable
a scheduled job. `sys.stderr.isatty()` is False for both `hooks run` (Claude Code captures hook
output, no TTY) and every `ccsched` job (`subprocess.run` with `capture_output=True`, no TTY) —
and for any *future* automated caller this codebase adds, with no allowlist maintenance required.
It is True exactly for the case this feature targets: a human running `ccst <something>` directly
in a terminal.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`/`sys`, `pytest`.

---

## Diagnosis / context for the engineer

1. **`ccst install-everything` already exists and already runs `ccst doctor` at the end of every
   invocation** (`src/cc_session_tools/cli/ccst.py`, `_cmd_install_everything`,
   `~line 1424`) — this plan does not invent a new command name or a new install step; it adds a
   marker-write to the existing one and a gate to the shared entrypoint.
2. **`ccst doctor` already has a real, wired-in FAIL-level check for the specific incident that
   motivated this plan** — stale/wrong-target `~/.claude/skills/*` symlinks left over from a
   package relocation (`check_skill_symlink()`, `src/cc_session_tools/lib/doctor.py:185`, wired
   into `run_all_checks()` at `~line 793`). That incident's actual root cause was not a missing
   check — it was that nothing prompted anyone to run `ccst doctor` or `ccst install-everything`
   after the `uv tool install --reinstall` that made the check start failing. This plan is that
   prompt.
3. **`sessions.db` is the established home for small persistent CLI state, not a new file.** Its
   module docstring (`src/cc_session_tools/lib/sessions_db.py:1-19`) explicitly documents that
   `doctor_mutes` was consolidated there "so all three tables share one schema and one file" —
   the same rationale applies to a fourth, similarly small table rather than inventing a bespoke
   JSON or `.db` file. `doctor_mutes.py` (`src/cc_session_tools/lib/doctor_mutes.py`) is the
   template this plan's new `install_sync.py` module follows: a thin public module whose functions
   open connections via `sessions_db.connect()`/`sessions_db.default_db_path()`.
4. **This repo's own `.claude/CLAUDE.md` data-store convention requires a queryable CLI surface
   for any new store** ("A store nobody can query except by opening it with a raw `sqlite3` shell
   isn't finished"). `doctor_mutes` satisfies this via `ccst doctor --list-mutes`/`--mute`/
   `--unmute`, not a dedicated `ccst mutes` subcommand. `install_sync` follows the same precedent:
   its one piece of state (the synced version) is exposed as a `ccst doctor` check result
   (Task 4) rather than a new subcommand — there is exactly one value to read, and `ccst doctor`
   is already the canonical place this codebase surfaces exactly this class of fact.
5. **`_cmd_install_everything`'s existing health-check call already discards its own return
   code** (`src/cc_session_tools/cli/ccst.py`, the `_cmd_doctor(argparse.Namespace(...))` call
   inside `_cmd_install_everything` — its result is never assigned or checked). Task 3 records the
   sync marker based on `overall_rc` from the five install *steps* only (skills/hooks/shell/
   claude-md/ccsched-jobs), matching this existing, deliberate scoping — the trailing health check
   is diagnostic output, not a gate on whether the install steps themselves succeeded.
6. **`ccst doctor` and `ccst install-everything` are both exempt from the interactive gate,
   unconditionally, regardless of TTY.** `install-everything` must always be runnable or there is
   no way to fix the state the gate is complaining about. `doctor` must always be runnable because
   it is the diagnostic tool — Task 4's new check already tells the user the same thing the gate
   would, inside doctor's full report; blocking `ccst doctor` itself on the very condition it is
   meant to diagnose would be self-defeating (the user could not even see *why* something is
   wrong).
7. **First-run behaviour after this ships is deliberate, not a bug.** Every existing installation
   upgrading to the version that ships this feature will have no `install_sync` row yet (nobody
   could have written one before this code existed), so the very next interactive `ccst` command
   will trigger the gate once, telling the user to run `install-everything`. Since
   `install-everything --apply` is idempotent, this is a one-time, harmless nudge that sweeps every
   existing installation the same way a fresh one would be swept — not a regression to work around.
8. **Performance:** the gate's read (`install_sync.get_synced_version()`) only runs for
   `sys.stderr.isatty()` invocations — i.e., a human directly typing a command. `ccst hooks run
   <verb>`, invoked on every single tool call in every open Claude Code session, and every
   `ccsched` job, never reaches it (checked first, before the version comparison, so the SQLite
   open/close cost is never paid on those hot/automated paths).

## File structure

| File | Change |
|---|---|
| `src/cc_session_tools/lib/sessions_db.py` | `DDL` gains the `install_sync` table |
| `src/cc_session_tools/lib/install_sync.py` | **New.** `record_synced()`, `get_synced_version()`, `should_block_for_unsynced_install()` |
| `src/cc_session_tools/cli/ccst.py` | `_cmd_install_everything` records the marker on success (Task 3); `main()` gains the interactive gate (Task 5) |
| `src/cc_session_tools/lib/doctor.py` | New `check_install_everything_synced()`, wired into `run_all_checks()` |
| `tests/test_sessions_db.py` | Schema-table-count test updated |
| `tests/test_install_sync.py` | **New.** Tests for the new module |
| `tests/test_ccst_install_everything.py` | New test: successful `--apply` records the marker |
| `tests/test_ccst_doctor.py` | New tests for the doctor check |
| `tests/test_ccst_main_gate.py` | **New.** Tests for the `main()` gate (argv-level, not just the pure decision function) |

---

### Task 1: `install_sync` table + module (`record_synced`/`get_synced_version`)

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant), code-quality-reviewed (Approved —
no blocking issues; one Minor, non-blocking note that `record_synced`'s inline ISO-timestamp
formatting duplicates `sessions_db._now_iso()`'s logic, matching a pre-existing pattern already
repeated across several other modules in this codebase, not a regression this task introduced).
Commit: `b22f10d` on branch `f/20260815-ccst-install-sync-nudge`.

**A plan-review finding, fixed here rather than in a follow-up task:** an earlier draft of
`get_synced_version()` only wrapped `sessions_db.connect(path=path, readonly=True)` in a
`try/except sqlite3.OperationalError`, not the `SELECT` itself. `connect(readonly=True)` skips DDL
by design, so it succeeds against an already-existing `sessions.db` (every current installation
has one, from `session_tags`) — the `SELECT` then raises `sqlite3.OperationalError: no such table:
install_sync` uncaught, since the table doesn't exist until some *writer* connects. Verified live
against a real sqlite file seeded with only the pre-existing tables. This is exactly the
post-upgrade first-run state Diagnosis point 7 describes, except the actual gap is a missing
*table*, not just a missing *row* — without this fix, the very first `ccst` command (including
`ccst doctor`, which Diagnosis point 6 requires to always work) after upgrading to the version that
ships this feature would crash with a traceback instead of showing the intended nudge. The code and
test below already include the fix.

**Files:**
- Modify: `src/cc_session_tools/lib/sessions_db.py` (`DDL`, `~line 35-60`)
- Create: `src/cc_session_tools/lib/install_sync.py`
- Test: `tests/test_sessions_db.py`, `tests/test_install_sync.py`

- [ ] **Step 1: Write the failing tests**

Update `tests/test_sessions_db.py`'s existing schema test (it asserts a subset with `<=`, so
this addition doesn't strictly need to change it, but update it anyway to keep the table list
accurate for future readers):

```python
def test_schema_has_four_tables(db_path):
    sessions_db.write_tag("uuid-1", "my-feature", path=db_path)  # bootstrap schema
    conn = sqlite3.connect(str(db_path))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"session_tags", "sessions", "doctor_mutes", "install_sync"} <= names
```

(This replaces the existing `test_schema_has_three_tables` — same body, updated name/set/assert.)

Create `tests/test_install_sync.py`:

```python
"""Tests for cc_session_tools.lib.install_sync — the install-everything sync marker."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import install_sync


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.db"


def test_get_synced_version_returns_none_when_never_recorded(db_path: Path) -> None:
    assert install_sync.get_synced_version(path=db_path) is None


def test_record_then_get_round_trips(db_path: Path) -> None:
    install_sync.record_synced("2.4.0", path=db_path)
    assert install_sync.get_synced_version(path=db_path) == "2.4.0"


def test_record_synced_upserts_on_second_call(db_path: Path) -> None:
    install_sync.record_synced("2.4.0", path=db_path)
    install_sync.record_synced("2.5.0", path=db_path)
    assert install_sync.get_synced_version(path=db_path) == "2.5.0"


def test_get_synced_version_on_nonexistent_db_returns_none(db_path: Path) -> None:
    """db_path is never created by this test - get_synced_version must not
    raise or create the file just to read from it (matches the established
    doctor_mutes.load_mutes graceful-degradation pattern)."""
    assert install_sync.get_synced_version(path=db_path) is None
    assert not db_path.exists()


def test_get_synced_version_on_pre_upgrade_db_missing_table_returns_none(db_path: Path) -> None:
    """The realistic first-run-after-upgrade case: every existing installation
    already has a sessions.db (session_tags/sessions/doctor_mutes), just not
    the install_sync table this feature adds. connect(readonly=True) skips
    DDL by design, so the SELECT itself must handle 'no such table', not just
    a missing file - a naive implementation that only wraps connect() in
    try/except raises here instead of returning None."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE session_tags (uuid TEXT PRIMARY KEY, tag TEXT, updated_at TEXT)")
    conn.commit()
    conn.close()

    assert install_sync.get_synced_version(path=db_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_sync.py tests/test_sessions_db.py -k "install_sync or schema_has_four" -v`
Expected: FAIL — `cc_session_tools.lib.install_sync` does not exist yet; `test_schema_has_four_tables`
fails because the `install_sync` table doesn't exist yet.

- [ ] **Step 3: Add the table and the module**

In `src/cc_session_tools/lib/sessions_db.py`, add to `DDL` (after the existing `doctor_mutes`
table):

```python
CREATE TABLE IF NOT EXISTS install_sync (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

(A generic one-row-per-key table, matching `doctor_mutes`' minimal shape, rather than a
single-column single-row table — leaves room for a second key later, e.g. a per-step sync
timestamp, without another schema change. Only one key, `"synced_version"`, is used by this plan.)

Create `src/cc_session_tools/lib/install_sync.py`:

```python
"""Tracks the last ccst version for which `ccst install-everything --apply`
succeeded — lets `main()` nudge an interactive user to re-run it after an
upgrade, and lets `ccst doctor` report the same fact as a check result.

Backed by the install_sync table in sessions.db, following the same
established pattern as doctor_mutes.py (see its module docstring and
sessions_db.py's): small persistent CLI state belongs in the shared
sessions.db file, not a bespoke JSON/db file per subsystem.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import sessions_db

_SYNCED_VERSION_KEY = "synced_version"


def get_synced_version(*, path: Path | None = None) -> str | None:
    """Return the version `install-everything --apply` last succeeded for,
    or None if it has never been recorded.

    Covers two distinct "never recorded" states, both returning None: no
    sessions.db file at all (fresh machine), and a sessions.db that predates
    this table (every existing installation upgrading to the version that
    ships this feature — session_tags/sessions/doctor_mutes already exist,
    but install_sync doesn't yet). connect(readonly=True) skips DDL by
    design (it must not create/migrate a store it's only meant to read), so
    the second case reaches the SELECT and must be caught there too, not
    just around connect() itself.
    """
    try:
        conn = sessions_db.connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(
            "SELECT value FROM install_sync WHERE key = ?", (_SYNCED_VERSION_KEY,)
        ).fetchone()
        return row["value"] if row is not None else None
    except sqlite3.OperationalError:
        return None  # e.g. "no such table: install_sync" - a pre-upgrade sessions.db
    finally:
        conn.close()


def record_synced(version: str, *, path: Path | None = None) -> None:
    """Record that `install-everything --apply` just succeeded for `version`."""
    conn = sessions_db.connect(path=path)
    try:
        conn.execute(
            "INSERT INTO install_sync (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (_SYNCED_VERSION_KEY, version, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_sync.py tests/test_sessions_db.py -v`
Expected: PASS (both files — confirms no regression on the existing `sessions_db` suite)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/sessions_db.py src/cc_session_tools/lib/install_sync.py \
        tests/test_install_sync.py tests/test_sessions_db.py
git commit -m "feat(install-sync): add the install_sync marker table and module

Records the ccst version for which 'install-everything --apply' last
succeeded, in a new install_sync table in the existing sessions.db file -
the same established pattern doctor_mutes already uses for small
persistent CLI state, rather than a new bespoke file. Read-side
(get_synced_version) degrades gracefully on a not-yet-created db, matching
doctor_mutes.load_mutes' existing convention. Not yet wired into any
command - this commit only adds the storage primitive."
```

---

### Task 2: `should_block_for_unsynced_install()` — the pure decision function

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant), code-quality-reviewed (Approved —
check order independently confirmed to have no correctness effect, and each exemption test
independently confirmed to genuinely isolate the property it claims to, not just happen to land on
the right result some other way; one Minor, addressed — the docstring now also explains the
`hooks run` exemption, not just the `is_interactive`/exempt-nouns ones). Commit: `d445c3b` on
branch `f/20260815-ccst-install-sync-nudge`.

**Files:**
- Modify: `src/cc_session_tools/lib/install_sync.py`
- Test: `tests/test_install_sync.py`

Kept separate from Task 1 so its many edge cases (interactive vs not, every exemption, matched vs
mismatched vs never-synced) get their own focused test pass, and separate from Task 5 (the actual
`main()` wiring) so the decision logic is fully unit-testable without touching `sys.argv`/argparse
at all.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_install_sync.py`:

```python
# ---------- should_block_for_unsynced_install ----------

def test_blocks_when_versions_differ_and_interactive() -> None:
    assert install_sync.should_block_for_unsynced_install(
        noun="pdata", verb="list",
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=True,
    )


def test_blocks_when_never_synced_and_interactive() -> None:
    assert install_sync.should_block_for_unsynced_install(
        noun="skills", verb="install",
        installed_version="2.4.0", synced_version=None,
        is_interactive=True,
    )


def test_does_not_block_when_versions_match() -> None:
    assert not install_sync.should_block_for_unsynced_install(
        noun="pdata", verb="list",
        installed_version="2.4.0", synced_version="2.4.0",
        is_interactive=True,
    )


def test_does_not_block_when_not_interactive() -> None:
    """The core safety property: a stale install must never block a
    non-interactive caller (a hook, a ccsched job, any future automation),
    regardless of noun/verb."""
    assert not install_sync.should_block_for_unsynced_install(
        noun="pdata", verb="verify",
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=False,
    )


def test_does_not_block_hooks_run_even_if_somehow_interactive() -> None:
    """Belt-and-braces: hooks run is exempt by name too, not just by the
    is_interactive=False it will always actually see in practice."""
    assert not install_sync.should_block_for_unsynced_install(
        noun="hooks", verb="run",
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=True,
    )


def test_does_not_block_install_everything_itself() -> None:
    assert not install_sync.should_block_for_unsynced_install(
        noun="install-everything", verb=None,
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=True,
    )


def test_does_not_block_doctor() -> None:
    assert not install_sync.should_block_for_unsynced_install(
        noun="doctor", verb=None,
        installed_version="2.4.0", synced_version="2.3.0",
        is_interactive=True,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_sync.py -k should_block -v`
Expected: FAIL — `should_block_for_unsynced_install` does not exist yet (`AttributeError`).

- [ ] **Step 3: Implement it**

Add to `src/cc_session_tools/lib/install_sync.py`:

```python
_EXEMPT_NOUNS = frozenset({"install-everything", "doctor"})


def should_block_for_unsynced_install(
    *,
    noun: str | None,
    verb: str | None,
    installed_version: str,
    synced_version: str | None,
    is_interactive: bool,
) -> bool:
    """True if main() should abort this invocation with an install-everything
    nudge instead of dispatching it.

    is_interactive must be False for every automated caller (a Claude Code
    hook via `ccst hooks run`, a ccsched job, any future scheduled/scripted
    caller) - this is the primary safety property, checked first. Exempt
    nouns (install-everything, the fix; doctor, the diagnostic tool) are
    never blocked regardless of interactivity, so the user always has a way
    to see or fix the state this function is protecting against.
    """
    if not is_interactive:
        return False
    if noun in _EXEMPT_NOUNS:
        return False
    if noun == "hooks" and verb == "run":
        return False
    return synced_version != installed_version
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_sync.py -v`
Expected: PASS (full file)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/install_sync.py tests/test_install_sync.py
git commit -m "feat(install-sync): add should_block_for_unsynced_install decision function

Pure function, no argv/sys dependency, so every exemption and edge case
(interactive vs not, matched/mismatched/never-synced version, each exempt
noun) gets a direct unit test. is_interactive=False short-circuits first -
the core safety property this whole feature depends on: no automated
caller (hooks run, a ccsched job, any future one) is ever blocked,
regardless of noun/verb, because none of them have a real TTY on stderr."
```

---

### Task 3: `ccst install-everything --apply` records the marker on success

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant), code-quality-reviewed (Approved —
isolation from the real machine's `sessions.db` independently verified by hashing it before/after
the new tests; the "does a partial-failure apply leave a stale-but-looks-synced marker" scenario
independently reproduced by hand and confirmed to be a non-issue, since `record_synced` never runs
unless `overall_rc == 0`). Commit: `2d439a1` on branch `f/20260815-ccst-install-sync-nudge`.
(Unrelated, pre-existing, out-of-scope observation from the reviewer: `_cmd_hooks_install` raises
an unhandled traceback rather than a clean non-zero exit for a malformed settings.json target —
not touched by this commit, not fixed here.)

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py` (`_cmd_install_everything`, `~line 1424-1507`)
- Test: `tests/test_ccst_install_everything.py`

- [ ] **Step 1: Write the failing test**

This file's tests run `ccst` as a real subprocess (`_run()`, `~line 10`), never in-process — there
is no `tmp_home`/`patched_targets` fixture to adapt. Follow the exact pattern
`test_install_everything_registers_bundled_ccsched_jobs` (`~line 148`) already uses: isolated
apply-targets via `_isolated_apply_args(tmp_path)`, plus `CCST_DATA_HOME` in the subprocess's `env`
so `sessions.db` (via `paths.data_home()`) also lands under `tmp_path` instead of the real
`~/.local/share/claude/`. Add:

```python
def test_apply_records_synced_version(tmp_path: Path) -> None:
    from cc_session_tools import __version__ as version
    from cc_session_tools.lib import install_sync

    env = os.environ.copy()
    env["CCST_DATA_HOME"] = str(tmp_path / "data-home")

    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *_isolated_apply_args(tmp_path)],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent), env=env,
    )

    assert result.returncode == 0
    assert install_sync.get_synced_version(
        path=tmp_path / "data-home" / "sessions.db"
    ) == version


def test_dry_run_does_not_record_synced_version(tmp_path: Path) -> None:
    from cc_session_tools.lib import install_sync

    env = os.environ.copy()
    env["CCST_DATA_HOME"] = str(tmp_path / "data-home")

    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "install-everything", "--no-pypi"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent), env=env,
    )

    assert result.returncode == 0
    assert install_sync.get_synced_version(path=tmp_path / "data-home" / "sessions.db") is None
```

(Confirm the exact import path for `__version__` — `cli/ccst.py` may re-export it, or it may live
at `cc_session_tools/__init__.py`; grep for `__version__ =` first rather than guessing. `--no-pypi`
is required on the dry-run test — omitting it, as an earlier draft of this snippet did, makes the
test issue a real PyPI network request, matching why `test_no_pypi_flag_accepted`/
`test_section_headers_present` already always pass it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_install_everything.py -k "synced_version" -v`
Expected: FAIL — `install_sync.get_synced_version(...)` returns `None` after `--apply` (nothing
records it yet).

- [ ] **Step 3: Record the marker on success**

In `src/cc_session_tools/cli/ccst.py`'s `_cmd_install_everything`, after the steps loop computes
`overall_rc` and before the trailing health-check block:

```python
    if apply and overall_rc == 0:
        from cc_session_tools.lib import install_sync
        install_sync.record_synced(__version__)
```

(Only on `--apply` with every step successful — a dry run or a partially-failed apply must not
claim the install is synced. `__version__` is already imported at module level in this file, used
a few lines below by `_cmd_doctor`'s `installed_version=__version__`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_install_everything.py -v`
Expected: PASS (full file, confirms no regression on the existing install-everything tests)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_install_everything.py
git commit -m "feat(install-everything): record the sync marker on a successful --apply

Only when apply=True and every install step (skills/hooks/shell/claude-md/
ccsched-jobs) returned 0 - a dry run or a partially-failed apply must not
claim the install is synced to this version. The trailing health-check
call's own return code is deliberately not part of this gate, matching its
existing, already-discarded return value a few lines below."
```

---

### Task 4: `ccst doctor` check for sync state

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant), code-quality-reviewed (Approved —
independently confirmed `ccst doctor --no-pypi` runs cleanly end-to-end against a `sessions.db`
with no `install_sync` table at all, and that a genuinely-fresh machine and a pre-upgrade machine
both collapse to the same accurate WARN text, matching an existing precedent for the same collapse
elsewhere in `doctor.py`'s `check_data_stores`). Commit: `87732b4` on branch
`f/20260815-ccst-install-sync-nudge`.

**Files:**
- Modify: `src/cc_session_tools/lib/doctor.py` (new `check_install_everything_synced()`;
  `run_all_checks()`)
- Modify: `src/cc_session_tools/cli/ccst.py` (`_cmd_doctor`'s `run_all_checks(...)` call site)
- Test: `tests/test_ccst_doctor.py`

- [ ] **Step 1: Write the failing tests**

Add near the existing `check_pypi_version` tests in `tests/test_ccst_doctor.py`:

```python
def test_check_install_synced_ok_when_versions_match() -> None:
    result = doctor.check_install_everything_synced(
        installed_version="2.4.0", synced_version="2.4.0"
    )
    assert result.status == doctor.Status.OK


def test_check_install_synced_warns_when_never_synced() -> None:
    result = doctor.check_install_everything_synced(
        installed_version="2.4.0", synced_version=None
    )
    assert result.status == doctor.Status.WARN
    assert "install-everything" in result.reason


def test_check_install_synced_warns_when_stale() -> None:
    result = doctor.check_install_everything_synced(
        installed_version="2.4.0", synced_version="2.3.0"
    )
    assert result.status == doctor.Status.WARN
    assert "2.3.0" in result.reason
    assert "2.4.0" in result.reason
```

(Match this file's existing import style for `doctor` - check the top of the file for whether it's
`from cc_session_tools.lib import doctor` or `from cc_session_tools.lib.doctor import ...` and
follow suit.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_doctor.py -k check_install_synced -v`
Expected: FAIL — `check_install_everything_synced` does not exist yet.

- [ ] **Step 3: Implement the check and wire it in**

Add to `src/cc_session_tools/lib/doctor.py`, near `check_pypi_version`:

```python
def check_install_everything_synced(
    installed_version: str, synced_version: str | None
) -> CheckResult:
    """WARN (not FAIL - always self-recoverable with one command, same
    severity rationale as check_ccsched_job_registered) if the running ccst
    version doesn't match the version 'install-everything --apply' last
    succeeded for."""
    name = "install:synced"
    if synced_version == installed_version:
        return CheckResult(
            name=name, status=Status.OK,
            reason=f"install-everything last synced at {installed_version}",
        )
    if synced_version is None:
        return CheckResult(
            name=name, status=Status.WARN,
            reason=(
                f"installed {installed_version}, install-everything has never been run — "
                "run `ccst install-everything --apply`"
            ),
        )
    return CheckResult(
        name=name, status=Status.WARN,
        reason=(
            f"installed {installed_version}, install-everything last synced at "
            f"{synced_version} — run `ccst install-everything --apply`"
        ),
    )
```

In `run_all_checks()` (same file), add a call near the PyPI version check at the end:

```python
    # Install-everything sync state
    results.append(
        check_install_everything_synced(installed_version, synced_version)
    )
```

This needs a new `synced_version: str | None = None` parameter on `run_all_checks()`'s signature
(add it near the existing `installed_version` parameter).

In `src/cc_session_tools/cli/ccst.py`'s `_cmd_doctor`, pass it through at the `run_all_checks(...)`
call site:

```python
    from cc_session_tools.lib import install_sync

    results = run_all_checks(
        installed_version=__version__,
        synced_version=install_sync.get_synced_version(),
        ...  # existing args unchanged
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: PASS (full file — confirms `run_all_checks`'s existing tests, which don't pass
`synced_version`, still work via the new parameter's default)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/doctor.py src/cc_session_tools/cli/ccst.py tests/test_ccst_doctor.py
git commit -m "feat(doctor): add an install:synced check for the install-everything marker

WARN (not FAIL) when the running ccst version doesn't match the version
install-everything --apply last succeeded for, or when it's never been
run at all - same severity rationale as the existing ccsched-job checks:
always self-recoverable with one command, not a sign of corrupted data."
```

---

### Task 5: The interactive gate in `main()`

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py` (`main()`, `~line 2176-2277`)
- Test: `tests/test_ccst_main_gate.py`

**A plan-review finding, fixed here:** an earlier draft called `install_sync.get_synced_version()`
unconditionally, before checking `is_interactive`, and only used `is_interactive` when calling
`should_block_for_unsynced_install(...)` afterwards. That directly contradicted Diagnosis point 8
("the marker read only runs for `sys.stderr.isatty()` invocations... checked first") — every `ccst
hooks run <verb>` and every `ccsched` job would have paid the SQLite open/close on every single
invocation, and would have hit Task 1's table-missing crash on every pre-upgrade machine. The code
below reads the marker only when `is_interactive` is already `True`.

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant), code-quality-reviewed by an
independent Opus pass given this is the highest-stakes commit in the plan — found one real,
Important regression, fixed in a review round: `get_synced_version()` only caught
`sqlite3.OperationalError`, but a corrupt (not-a-valid-SQLite-file) `sessions.db` raises
`sqlite3.DatabaseError` from the `SELECT` itself (`sqlite3.connect()` opens lazily and doesn't
validate the file), which would have crashed every interactive `ccst` command — including
`install-everything` and `doctor`, the two commands this gate is supposed to always leave working.
Fixed by widening the `except` around the `SELECT` to `sqlite3.DatabaseError` (the shared parent of
`OperationalError`, so the fix subsumes the existing case too), with a new regression test
(`test_get_synced_version_on_corrupt_db_returns_none`). Also fixed in the same round: a missing
test for the "never synced" (`synced_version is None`) message branch at the `main()` level — every
existing installation hits exactly this branch on its first post-upgrade invocation, and it had no
argv-level test (`test_blocks_interactive_command_when_never_synced`); and a code comment that
referenced this plan document by name, which isn't tracked in the repo and won't exist for anyone
reading the shipped code — reworded to be self-contained. Two further Minor suggestions (a
micro-optimisation to the lazy import, cosmetic message-prefix consistency) were left as-is —
verified the "optimisation" saves nothing (the module is imported unconditionally either way, for
the `should_block_for_unsynced_install` call), and the prefix-omission was already judged
defensible in the original review (a nudge, not an error). Commit: `e6b4e91` on branch
`f/20260815-ccst-install-sync-nudge`.

**Why this needs an argv-level test, not just Task 2's unit tests:** Task 2 proved the decision
function is correct in isolation. This task proves `main()` actually calls it with the right
`noun`/`verb`/`is_interactive` values and actually exits before dispatching — the exact kind of gap
Task 3 in the `uv-aware-command-cache` plan warned about (a normalise()-only unit test passing
while the real `run()`-equivalent pipeline never gets exercised).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ccst_main_gate.py`:

```python
"""Tests for the install-sync interactive gate in ccst.cli.ccst.main()."""
from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from cc_session_tools.cli import ccst


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "sessions.db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    return p


def test_blocks_interactive_command_on_stale_install(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    # doctor itself is exempt (see test_does_not_block_doctor_when_stale below) -
    # use a non-exempt noun/verb here:
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_skills_install")

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 1
    dispatched.assert_not_called()


def test_does_not_block_when_synced(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced(ccst.__version__, path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_skills_install", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_does_not_block_when_not_interactive(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch("sys.stderr.isatty", return_value=False)
    dispatched = mocker.patch.object(ccst, "_cmd_skills_install", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_does_not_block_hooks_run_even_when_stale_and_interactive(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "hooks", "run", "some-hook"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_hooks_run", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_does_not_block_install_everything_when_stale(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "install-everything"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_install_everything", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_does_not_block_doctor_when_stale(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "doctor"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    dispatched = mocker.patch.object(ccst, "_cmd_doctor", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


def test_block_message_mentions_install_everything(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture, capsys
) -> None:
    from cc_session_tools.lib import install_sync

    install_sync.record_synced("0.0.1-not-current", path=db_path)
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch("sys.stderr.isatty", return_value=True)
    mocker.patch.object(ccst, "_cmd_skills_install")

    with pytest.raises(SystemExit):
        ccst.main()

    err = capsys.readouterr().err
    assert "install-everything --apply" in err
```

(Adapt `monkeypatch.setattr(ccst.sys, "argv", ...)` to however this file's neighbours already patch
`sys.argv` for `main()`-level tests, if a different convention already exists elsewhere in this
test suite — grep for an existing `main()` test first, e.g. `test_help_flag` in
`tests/test_ccst_install_everything.py`, to check.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_main_gate.py -v`
Expected: FAIL — every test that expects `exc.value.code == 1` on a stale install currently sees
the command dispatch normally instead (no gate exists yet); the not-yet-existing gate means every
`dispatched.assert_not_called()` fails too.

- [ ] **Step 3: Implement the gate**

In `src/cc_session_tools/cli/ccst.py`'s `main()`, immediately after the existing `args.noun is
None` early-return block, and before the first `if args.noun == "hooks":`:

```python
    from cc_session_tools.lib import install_sync

    is_interactive = sys.stderr.isatty()
    # Read the marker only when interactive: `ccst hooks run <verb>` fires on
    # every single tool call in every open Claude Code session, and ccsched
    # jobs run on a schedule - neither has a TTY on stderr, and neither may
    # pay a SQLite open/close on every invocation just to compute a value
    # should_block_for_unsynced_install would immediately discard anyway
    # (its own is_interactive=False check returns False before ever looking
    # at synced_version). Diagnosis point 8 depends on this ordering.
    synced_version = install_sync.get_synced_version() if is_interactive else None
    if install_sync.should_block_for_unsynced_install(
        noun=args.noun,
        verb=getattr(args, "verb", None),
        installed_version=__version__,
        synced_version=synced_version,
        is_interactive=is_interactive,
    ):
        if synced_version is None:
            state = "install-everything has never been run for this installation"
        else:
            state = f"install-everything was last synced at {synced_version}"
        print(
            f"ccst is installed at {__version__}, but {state}.\n"
            "Skills, hooks, shell functions, scheduled jobs, and CLAUDE.md config may be "
            "out of sync with this version.\n\n"
            "Run: ccst install-everything --apply\n",
            file=sys.stderr,
        )
        sys.exit(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_main_gate.py -v`
Expected: PASS (full file)

Then run the whole CLI test surface to confirm no other `main()`-level test broke — every existing
test that calls `main()` (directly or via subprocess) does so on a machine/tmp state where
`get_synced_version()` returns `None` unless it explicitly records one, which per Task 2 means
`should_block_for_unsynced_install` returns `True` whenever `is_interactive` is (accidentally)
`True` in a test's execution environment. Check for exactly this before considering this task
done:

Run: `uv run pytest -k "ccst" -v 2>&1 | tail -60`
Expected: PASS. If any existing test now fails because it invokes `main()` directly under pytest
(where `sys.stderr.isatty()` is normally `False` - pytest captures stderr by default, which is
not a TTY - but double-check rather than assume), that test needs `CCST_SESSIONS_DIR` pointed at
an empty `tmp_path` plus either an explicit `synced_version` record or an `isatty` patch, matching
this task's own new fixtures.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_main_gate.py
git commit -m "feat(cli): block interactive commands on a stale install-everything sync

main() now checks should_block_for_unsynced_install() before dispatching
any command. sys.stderr.isatty() is the interactivity test - False for
every automated caller (a Claude Code hook via 'ccst hooks run', a
ccsched job, capsys-captured test runs) regardless of noun/verb, so none
of them are ever blocked. install-everything and doctor are exempt by
name so the user always has a way to see or fix the state this gate is
protecting against."
```

---

### Task 6: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

```bash
uv run pytest -q
```

Expected: all pass, 0 failures.

- [ ] **Step 2: Manual smoke test (recommended — this changes every ccst invocation's entrypoint)**

From a real interactive shell, using the worktree's own installed environment (`uv run python`,
not a bare `python3` that may not have this package installed at all):

```bash
# Simulate a stale install:
uv run python -c "
from cc_session_tools.lib import install_sync
install_sync.record_synced('0.0.1-test')
"
ccst skills install   # expect: the nudge message + exit 1, no dispatch
ccst doctor            # expect: runs normally, and its output includes a WARN 'install:synced' line
ccst install-everything --apply   # expect: runs normally, and re-syncs the marker
ccst skills install    # expect: now runs normally again (no nudge)
```

Also confirm the non-interactive path is unaffected — redirecting stdin (`echo |`) does NOT make
this true; `sys.stderr.isatty()` cares about stderr, which stays attached to the terminal under a
stdin-only redirect, so that would still be blocked. Redirect stderr itself instead:

```bash
uv run python -c "
from cc_session_tools.lib import install_sync
install_sync.record_synced('0.0.1-test')
"
ccst skills install 2>/dev/null   # stderr not a TTY here - confirm this still dispatches
                                   # normally (check exit code / actual effect, not stderr text)
```

---

---

## Final holistic review (post-Task-6, before PR)

An Opus final reviewer checked the whole 5-commit branch together (full suite, a real end-to-end
lifecycle simulation over a genuine pty, and the safety invariant under composition) and found four
Important items, all fixed in a follow-up commit before the PR was opened:

- **`ccst repair` and `ccst migrate` were not exempt from the gate.** This repo's own
  pending-migration doctor output tells users to run `ccst migrate all` "from a plain terminal",
  and `ccst repair sessions` is literally the store-corruption fix tool — including for corruption
  of the sync marker's own store. Added both to `_EXEMPT_NOUNS`.
- **`install-everything --apply` crashed at `record_synced()` on a corrupt `sessions.db`**, after
  all five install steps had already succeeded. Wrapped in `try/except sqlite3.DatabaseError` with
  a warning instead of a crash.
- **Chasing that crash surfaced a second, genuinely pre-existing bug** (not introduced by this
  plan, present on `main`) on the same code path: `_count_new_store_rows` in `doctor.py` only
  caught `sqlite3.OperationalError` around its row-count `SELECT`s, not `sqlite3.DatabaseError` —
  so a corrupt `sessions.db` still crashed `install-everything`'s own trailing health check via an
  unrelated check (`check_pending_data_store_migration`). Fixed this one specific site because it's
  load-bearing for this plan's own safety property (a corrupt store must never remove every
  interactive way out); a broader audit of similar sites elsewhere in `doctor.py`/`sessions_db.py`
  (e.g. `check_sessions_project_dir_absolute` has the identical gap) is explicitly out of scope.
- **Version bump / CHANGELOG / README were never touched by any of the 5 Task commits.** Fixed:
  2.4.0 bump, CHANGELOG entry, and a README update covering both the new nudge behaviour and the
  new `install:synced` doctor check.

Two Minor items were also fixed: a missing test proving `install:synced` is actually wired into
`run_all_checks()`/`_cmd_doctor` (not just unit-tested in isolation — the same "normalise()-only
test never proves the real pipeline" gap the `uv-aware-command-cache` plan's own Task 3 warns
about), and `record_synced()` reusing `sessions_db._now_iso()` instead of duplicating its
timestamp-formatting logic inline.

**A second final-review pass then found the corrupt-sessions.db survival claim was still false in
two more places**, both reached only once the first round's `record_synced()` fix stopped masking
them: `check_sessions_project_dir_absolute` (in `doctor.py`, reached by both `ccst doctor` and
`install-everything`'s trailing health check) and `_cmd_repair_sessions` itself both still
tracebacked on a corrupt `sessions.db`, because `sqlite3.connect()` opens lazily and a corrupt file
only fails once a query actually touches it — the exact same root cause as the first round's fix,
at two sites that root cause hadn't yet reached before. This closes the loop the whole plan's safety
argument depends on: `doctor`/`repair`/`migrate`/`install-everything` must all genuinely survive the
one failure mode (a corrupt sync-marker store) they're exempted from the gate specifically to let a
user recover from — not just be reachable and then crash anyway. Verified with real, un-mocked
corrupt-file subprocess tests for both sites, not unit-level mocks alone; a prior mocked-only test's
docstring had incorrectly claimed the doctor-side crash was already handled elsewhere, and was
corrected alongside the fix.

---

## Out of scope (deliberately, not oversights)

- **A dedicated `ccst install-sync` (or similar) query subcommand** — the one value this store
  holds is already surfaced via `ccst doctor`'s new check result, matching the `doctor_mutes`
  precedent (queried via `ccst doctor --list-mutes`, not its own subcommand). Revisit only if a
  second piece of state is ever added to this table that doctor's single-line report can't
  usefully express.
- **Auto-running `install-everything --apply` on the user's behalf** — the gate always requires an
  explicit, human-run command. Auto-applying changes to `~/.claude/settings.json`,
  `~/.claude/skills/*`, shell rc files, and `CLAUDE.md` without an explicit `--apply` from the user
  would be a much larger, separate trust decision this plan does not make.
- **Detecting `uv tool install` at the moment it runs** — there is no reliable post-install hook
  for `uv tool install`/`pip install`/`pipx install` to attach to (this was investigated and
  confirmed before choosing the version-marker approach: modern installers do not run arbitrary
  code as part of installation for security reasons). Comparing the running version against a
  persisted marker on next invocation is the general mechanism that works for `uv`, `pipx`, and
  plain `pip` alike, without needing installer-specific integration.
