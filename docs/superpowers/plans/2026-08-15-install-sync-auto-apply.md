# Install-sync auto-apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 2.4.0's nudge-and-block gate (which never fires, because no automated caller has
a TTY) with an auto-apply: the first non-exempt `ccst` invocation after an upgrade silently runs
the five install steps itself, tells the user on stderr that it did, and dispatches the requested
command anyway with its exit code untouched.

**Architecture:** `install_sync.py` keeps its pure/impure split but changes shape. Two pure
functions — `is_auto_sync_exempt()` (argv + env only, so an exempt caller never opens
`sessions.db`) and `decide_auto_sync()` (markers in hand → `SKIP_SYNCED` / `SKIP_BACKOFF` /
`APPLY`) — sit behind one impure wrapper, `ensure_synced()`, called from `main()` at the same site
2.4.0's gate occupied. `should_block_for_unsynced_install()` is deleted outright. The apply itself
reuses `_cmd_install_everything`'s five steps via a new parameterised core,
`run_install_everything(...)`, guarded by an exclusive, try-once, non-blocking lock file hoisted out
of `scheduler/lock.py` into a shared `lib/proc_lock.py`. A persistent failure is remembered in three
new rows of the existing `install_sync` KV table so it backs off for 6h instead of retrying on every
invocation, and `ccst doctor` FAILs (not WARNs) on that state.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`/`contextlib`/`io`/`os`/`threading`/`enum`/
`dataclasses`, `pytest`, `pytest-mock`.

**Spec:** `docs/superpowers/specs/2026-08-15-install-sync-auto-apply-design.md`. The spec is
authoritative. Section references below (§1-§11) point into it. Where this plan makes a decision the
spec left open, it says so explicitly under **Spec gap** — there are five, listed together in
"Spec gaps and the calls made" below.

---

## Diagnosis / context for the engineer (already established — not open questions)

1. **The 2.4.0 gate is dead code in practice.** `main()` guards it with
   `is_interactive = sys.stderr.isatty()` (`src/cc_session_tools/cli/ccst.py:2227`), and Claude
   Code's Bash tool allocates no TTY, so it is `False` for every `ccst` command an agent runs.
   Same for every `ccsched` job. The nudge is invisible to exactly the workflow it was built for.
   `is_interactive` does not appear anywhere in the new design.
2. **What actually goes stale on an upgrade is registration, not code.** `~/.claude/skills/*`
   symlinks and `ccst hooks run <verb>`'s dynamic import both resolve into
   `~/.local/share/uv/tools/cc-session-tools/lib/python3.13/site-packages/…`, which
   `uv tool install --reinstall` reuses in place. A hook or skill added in a newer version is not
   in `settings.json` / `~/.claude/skills/` until `install-everything` runs — so it silently never
   runs at all.
3. **Wheels have no post-install hook.** `pip`/`uv` run no arbitrary code after unpacking. The
   first `ccst` process to start after an upgrade is the earliest possible moment, and that is
   where this design puts the check.
4. **Every install step is a no-op-without-write when the config already matches** (§10 tabulates
   the blast radius per step, verified in source). Measured steady-state cost of the five steps:
   **16.8 ms**. Measured cost of the marker read on a carrier invocation: **0.56 ms**, against a
   **94 ms** bare `ccst --version` process start. The trailing `ccst doctor` health check is
   **~1.55 s**, dominated by its PyPI network call — which is why auto-apply passes
   `health_check=False`.
5. **The five step functions print with bare `print()` to stdout, and only ever write to
   `sys.stderr` on a path that also returns rc 1.** Verified by reading
   `_cmd_skills_install` (`ccst.py:153`), `_cmd_hooks_install` (`:315`), `_cmd_shell_install`
   (`:691`), `_cmd_claude_md_install` (`:745`), `_cmd_ccsched_jobs_install` (`:1402`). This is what
   makes §4's "output on success is exactly two stderr lines" achievable by capturing stdout alone.
6. **Auto-apply must never touch stdout.** `ccst sessions list --json` emits machine-readable
   stdout, and `scheduler/worker.py:105-111` carries a job's stdout into the ledger as its recorded
   *findings*. Interleaving install chatter there is a correctness bug, not a cosmetic one.
7. **Auto-apply must never change the invocation's exit code.** `ccsched`'s success/failure ledger
   auto-suspends a job after 10 consecutive failures (`worker.py:85-96`); a failed auto-apply that
   leaked into `$?` would start suspending healthy jobs.
8. **`hooks run` is exempt and self-heals via the scheduler, not via a hook** (§6). A SessionStart
   self-heal hook cannot bootstrap itself: a new hook verb is not in `settings.json` until
   `install-everything` has run. The backstop is `bundled_jobs.BUNDLED_CCSCHED_JOBS`'
   `pdata-verify-all` at `daily@03:00`, whose command is `("ccst", "pdata", "verify",
   "--all-projects")` — a fresh `ccst` subprocess on a non-exempt noun, i.e. a carrier. Worst-case
   self-heal latency with zero user action: ~24h.
9. **`tests/conftest.py`'s opt-out is not optional.** `tests/test_ccst_main_gate.py` calls
   `ccst.main()` in-process, and roughly fifteen modules shell out via
   `subprocess.run([sys.executable, "-m", "cc_session_tools.cli.ccst", …])`. Both groups rely today
   on `is_interactive=False` to skip the gate. Without `CCST_NO_AUTO_SYNC=1`, both would run a real
   five-step install against the developer's live `~/.claude` on every test run. This is why it is
   **Task 1**, landing before any auto-apply code exists.

---

## Spec gaps and the calls made

Five points the spec did not settle. Each is flagged again at the task that implements it.

| # | Gap | Call made |
|---|---|---|
| 1 | §4 specifies `run_install_everything(stream: TextIO, …)`, but the five step functions `print()` straight to stdout and take no stream parameter. | Wrap the step loop in `contextlib.redirect_stdout(stream)`. `stream=sys.stdout` is then a no-op passthrough for the `_cmd_install_everything` adapter, and `stream=io.StringIO()` captures everything for auto-apply — without touching any of the five functions. Task 6. |
| 2 | §5 asks for "an in-process flag so a single `ccst` process never retries", but `main()` calls `ensure_synced()` exactly once per process, so the flag is unobservable through the production call path. | Implement it anyway (module-level `_auto_sync_attempted`), with its own test asserting a second in-process call is a no-op. It is a real, testable behaviour rather than dead defensive code, and it is what makes `ensure_synced` safe to call from a second site later. Task 7. |
| 3 | §5 says a failed auto-apply records three keys, but does not say what happens when `record_failed_attempt` itself raises (the corrupt-store degenerate case it discusses only covers `record_synced`). | Catch `sqlite3.DatabaseError` around it and print the same `ccst repair sessions` pointer. Justified by §5's own explicit invariant: auto-apply is "never fatal". Task 7. |
| 4 | The spec's `install-everything.sh` note covers the closing "For future upgrades" line at L95, but the identical claim also appears in the file's usage header at L20-22. | Update both. Task 12. |
| 5 | §3 says the extracted `proc_lock` keeps `scheduler/lock.py`'s in-process `threading.Lock` TOCTOU guard, but the scheduler keys that dict on `job_id` while the generic helper only has a path. | Key on `str(path)`. Strictly finer-grained than `job_id` (two different scheduler dirs holding the same job id no longer share a thread lock), so no existing guarantee is weakened. Task 2. |

---

## Execution notes (read before Task 0)

- **The branch-policy hook is a repo-specific footgun.** `enforce-git-branch-policy.sh` blocks
  mutating git commands while on `main`, and it resolves the target directory by parsing the
  command string — it needs `cd <dir> && git …` on **one line**, not a bare newline before the
  `git` invocation. Every commit command in this plan is written that way. Do not reformat them.
- **Never run `uv tool install` from inside the worktree.** It overwrites the global install's
  source pointer and breaks all eight CLIs when the worktree is deleted. Use `uv run` for
  everything (`uv run pytest -q`, `uv run python -m cc_session_tools.cli.ccst …`).
- **One worktree, one branch, one PR** for the whole plan. The tasks are not independently
  shippable: Task 8 deletes `should_block_for_unsynced_install`, whose only production caller is
  the code Task 8 itself replaces, and Tasks 3-7 build the replacement that Task 8 needs. Splitting
  would leave `main` in a state where the gate is gone and the auto-apply is not yet wired.
- **`uv run pytest -q` must be green before any task is considered done**, not just the task's own
  test file. Verification is binary.
- **Ellipsis character:** §4's success message uses `…` (U+2026), not `...`. Copy the strings in
  this plan verbatim; the tests assert on them.

---

## File structure

| File | Change |
|---|---|
| `tests/conftest.py` | Autouse fixture also sets `CCST_NO_AUTO_SYNC=1` (Task 1) |
| `src/cc_session_tools/lib/proc_lock.py` | **New.** `LockHeld`, `exclusive_lock(path)` — the O_EXCL + pid-reclaim + thread-guard mechanism, hoisted (Task 2) |
| `src/cc_session_tools/lib/scheduler/lock.py` | `in_flight_lock` delegates to `proc_lock.exclusive_lock`; keeps `InFlightLockHeld` and its per-job path derivation (Task 2) |
| `src/cc_session_tools/lib/install_sync.py` | `FailedAttempt`, `record_failed_attempt`, `get_failed_attempt`, `record_synced` clears failure keys (Task 3); `is_auto_sync_exempt` (Task 4); `AutoSyncAction`, `decide_auto_sync` (Task 5); `ensure_synced` (Task 7); `should_block_for_unsynced_install` deleted (Task 8); module docstring (Task 10) |
| `src/cc_session_tools/cli/ccst.py` | `run_install_everything()` core + `_cmd_install_everything` adapter (Task 6); `main()` calls `ensure_synced` (Task 8); `_cmd_doctor` passes the failed attempt (Task 9); subparser `help=` (Task 10) |
| `src/cc_session_tools/lib/doctor.py` | `check_install_everything_synced` gains a `failed_attempt` param and a FAIL branch; `run_all_checks` threads it through (Task 9) |
| `tests/test_proc_lock.py` | **New.** Tests for the extracted lock (Task 2) |
| `tests/test_install_sync.py` | Extended: failure keys, `is_auto_sync_exempt`, `decide_auto_sync`, `ensure_synced` end-to-end. `should_block_*` tests deleted (Tasks 3-5, 7, 8) |
| `tests/test_ccst_install_everything.py` | Extended: `health_check=False` does not call doctor; stream capture (Task 6) |
| `tests/test_ccst_main_gate.py` | Rewritten — block-and-exit assertions no longer describe the product (Task 8) |
| `tests/test_ccst_doctor.py` | New FAIL-branch tests (Task 9) |
| `README.md`, `CHANGELOG.md`, `install-everything.sh`, `.claude/CLAUDE.md` | Tasks 11-13, 15 |
| `pyproject.toml` | 2.5.0 (Task 15) |

---

### Task 0: Worktree setup

**Files:** none (environment only)

- [ ] **Step 1: Create the worktree off a fresh `origin/main`**

```bash
cd ~/repos/claude-code-session-tools && git fetch origin
cd ~/repos/claude-code-session-tools && git worktree add .worktrees/install-sync-auto-apply -b f/20260815-install-sync-auto-apply origin/main
```

- [ ] **Step 2: Install dev dependencies in the worktree**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && uv sync --extra dev
```

- [ ] **Step 3: Confirm a green baseline before adding anything**

Run: `cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && uv run pytest -q`
Expected: all pass. If anything fails on a clean `origin/main`, fix that first — you own the
codebase while you are in it, and a pre-existing failure will mask a regression later.

All subsequent paths in this plan are relative to the worktree root.

---

### Task 1: `CCST_NO_AUTO_SYNC=1` in the autouse test fixture

**Why first:** from Task 8 onwards, any test that reaches `main()` — in-process or via
`subprocess.run([sys.executable, "-m", "cc_session_tools.cli.ccst", …])` — would otherwise run a
real five-step install against the developer's live `~/.claude`. Landing the opt-out before the
auto-apply exists means that window never opens. `monkeypatch.setenv` mutates `os.environ`, which
subprocesses inherit, so this one fixture covers both groups.

**Files:**
- Modify: `tests/conftest.py:20-26`
- Test: `tests/test_install_sync.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_install_sync.py`:

```python
# ---------- test-suite safety ----------

def test_conftest_opts_every_test_out_of_auto_sync() -> None:
    """The autouse fixture in tests/conftest.py must set CCST_NO_AUTO_SYNC=1
    for every test in this suite. Without it, any test that reaches ccst's
    main() - in-process or via subprocess - runs a real five-step install
    against the developer's live ~/.claude. Tests that genuinely exercise
    auto-sync delete this var themselves and redirect HOME/CCST_DATA_HOME/
    CCST_SESSIONS_DIR to tmp_path first."""
    import os

    assert os.environ.get("CCST_NO_AUTO_SYNC") == "1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_install_sync.py -k opts_every_test -v`
Expected: FAIL — `assert None == '1'`.

- [ ] **Step 3: Extend the autouse fixture**

In `tests/conftest.py`, replace the `_clean_session_root_env` fixture body:

```python
@pytest.fixture(autouse=True)
def _clean_session_root_env(monkeypatch):
    """Make sure no inherited env vars from the developer's shell leak into
    tests. Tests that need roots set must do so explicitly.

    CCST_NO_AUTO_SYNC=1 is set for the whole suite, not cleared: ccst's
    main() auto-applies `install-everything` when the installed version
    doesn't match the recorded sync marker, and dozens of tests reach main()
    either in-process or through `subprocess.run([sys.executable, "-m",
    "cc_session_tools.cli.ccst", ...])`. monkeypatch.setenv mutates
    os.environ, which those subprocesses inherit, so this one fixture covers
    both groups. Tests that exercise auto-sync on purpose delete the var
    themselves after redirecting HOME, CCST_DATA_HOME and CCST_SESSIONS_DIR
    at tmp_path.
    """
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    monkeypatch.delenv("CCX_DEBUG", raising=False)
    monkeypatch.setenv("CCST_NO_AUTO_SYNC", "1")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_sync.py -v && uv run pytest -q`
Expected: PASS both. The env var is inert today (nothing reads it yet) — this step is purely
about the ordering guarantee.

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add tests/conftest.py tests/test_install_sync.py && git commit -m "test: opt the whole suite out of install-sync auto-apply

Lands before the auto-apply itself so the window never opens. ccst's
main() is about to gain an auto-apply that runs the five install steps
against ~/.claude; dozens of tests reach main() in-process or through a
subprocess, and every one of them would otherwise mutate the developer's
real machine on each run. monkeypatch.setenv covers the subprocess group
too, since they inherit os.environ."
```

---

### Task 2: Extract `lib/proc_lock.py`; `scheduler/lock.py` delegates

§3. Two processes can reach `APPLY` at once (a 03:00 `ccsched` job and a Claude Bash call).
`write_json_atomic` stops file corruption, but a concurrent read-modify-write can still lose an
update. The mechanism needed is exactly `scheduler/lock.py`'s, so per this repo's one-source-of-
truth standard the second use is the point at which it gets hoisted.

**Spec gap 5:** §3 does not say what the extracted helper keys its in-process `threading.Lock`
dict on. The scheduler keys on `job_id`; the generic helper only has a path. **Call: key on
`str(path)`.** That is strictly finer-grained (two different `CC_SCHEDULER_DIR`s holding the same
job id stop sharing a thread lock, which is more correct, not less), and the existing tests
monkeypatch `CC_SCHEDULER_DIR` per test, so they are unaffected.

**Files:**
- Create: `src/cc_session_tools/lib/proc_lock.py`
- Modify: `src/cc_session_tools/lib/scheduler/lock.py` (whole file)
- Create: `tests/test_proc_lock.py`
- Test (must pass unchanged): `tests/scheduler/test_lock.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_proc_lock.py`:

```python
"""Tests for cc_session_tools.lib.proc_lock - the shared exclusive lock file."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from cc_session_tools.lib.proc_lock import LockHeld, exclusive_lock


def test_acquire_then_release_then_reacquire(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "x.lock"
    with exclusive_lock(path):
        assert path.exists()
    assert not path.exists()
    with exclusive_lock(path):  # released, so re-acquire works
        pass


def test_creates_missing_parent_directory(tmp_path: Path) -> None:
    """paths.data_home() is not guaranteed to exist on a fresh machine, and
    install_sync's lock lives directly inside it."""
    path = tmp_path / "does" / "not" / "exist" / "x.lock"
    with exclusive_lock(path):
        assert path.exists()


def test_second_acquire_raises_lock_held(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    with exclusive_lock(path):
        with pytest.raises(LockHeld):
            with exclusive_lock(path):
                pass


def test_distinct_paths_lock_independently(tmp_path: Path) -> None:
    with exclusive_lock(tmp_path / "a.lock"):
        with exclusive_lock(tmp_path / "b.lock"):
            pass


def test_stale_lock_naming_a_dead_pid_is_reclaimed(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    path.write_text(json.dumps({"pid": 2_000_000_000, "started": "x"}))
    with exclusive_lock(path):  # reclaimed
        assert json.loads(path.read_text())["pid"] == os.getpid()


def test_lock_file_records_the_holder_pid(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    with exclusive_lock(path):
        assert json.loads(path.read_text())["pid"] == os.getpid()


def test_race_has_exactly_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    winners = 0
    guard = threading.Lock()
    barrier = threading.Barrier(8)

    def worker() -> None:
        nonlocal winners
        barrier.wait()
        try:
            with exclusive_lock(path):
                with guard:
                    winners += 1
                import time
                time.sleep(0.02)
        except LockHeld:
            pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert winners == 1


def test_custom_exception_type_and_label(tmp_path: Path) -> None:
    """scheduler/lock.py needs to keep raising InFlightLockHeld with its own
    message wording - the extraction must not flatten every caller onto the
    generic type."""
    class Mine(LockHeld):
        pass

    path = tmp_path / "x.lock"
    with exclusive_lock(path, held_exc=Mine, label="widget 'w1'"):
        with pytest.raises(Mine) as exc:
            with exclusive_lock(path, held_exc=Mine, label="widget 'w1'"):
                pass
    assert "widget 'w1'" in str(exc.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_proc_lock.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: cc_session_tools.lib.proc_lock`.

- [ ] **Step 3: Create the module**

Create `src/cc_session_tools/lib/proc_lock.py`:

```python
# src/cc_session_tools/lib/proc_lock.py
"""Exclusive, try-once inter-process lock file. Never waits.

Atomicity from os.open(O_CREAT|O_EXCL): exactly one caller creates the file for
a given path. A contender raises LockHeld unless the recorded holder pid is
dead, in which case the stale lock is reclaimed. Callers that lose are expected
to skip their work, not to retry or block.

An in-process threading.Lock, keyed on the lock path, guards the O_EXCL
create+check sequence to close a TOCTOU window: without it, a stale-lock
reclaim (FileExistsError -> holder-dead check -> unlink -> re-create) can
interleave with another thread's create attempt inside the same process,
producing two simultaneous holders.

Two callers share this: scheduler/lock.py's per-job in-flight lock, and
install_sync.ensure_synced's auto-apply lock.
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Keyed on str(path) rather than on any caller-level id: the path is the thing
# O_EXCL actually contends on, so two callers that resolve to the same file
# always share a thread lock and two that don't never do.
_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


class LockHeld(RuntimeError):
    """Raised when the lock file is held by a live process (or another thread)."""


def _thread_lock_for(key: str) -> threading.Lock:
    with _thread_locks_guard:
        if key not in _thread_locks:
            _thread_locks[key] = threading.Lock()
        return _thread_locks[key]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours to signal
    return True


def _read_holder(path: Path) -> int | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    pid = data.get("pid")
    return int(pid) if isinstance(pid, int) else None


def _try_create(path: Path) -> int:
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    payload = json.dumps(
        {"pid": os.getpid(), "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    )
    os.write(fd, payload.encode())
    return fd


@contextmanager
def exclusive_lock(
    path: Path,
    *,
    held_exc: type[LockHeld] = LockHeld,
    label: str | None = None,
) -> Iterator[None]:
    """Hold `path` exclusively for the body, or raise `held_exc` immediately.

    `held_exc`/`label` exist so a caller can keep its own exception type and
    message wording (scheduler/lock.py's InFlightLockHeld, "in-flight lock for
    'job-a' held by live pid 123") without reimplementing the mechanism.
    """
    what = label or str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tlock = _thread_lock_for(str(path))
    if not tlock.acquire(blocking=False):
        raise held_exc(f"{what} held by another thread")
    try:
        try:
            fd = _try_create(path)
        except FileExistsError:
            holder = _read_holder(path)
            if holder is not None and pid_alive(holder):
                raise held_exc(f"{what} held by live pid {holder}") from None
            path.unlink(missing_ok=True)  # stale -> reclaim
            try:
                fd = _try_create(path)
            except FileExistsError:
                # Another process reclaimed and reacquired between our unlink
                # and our create - we lose this race: treat as held.
                raise held_exc(f"{what} re-acquired concurrently") from None
        try:
            yield
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)
    finally:
        tlock.release()
```

Replace `src/cc_session_tools/lib/scheduler/lock.py` entirely with the delegating version:

```python
# src/cc_session_tools/lib/scheduler/lock.py
"""Per-job in-flight lock at <scheduler-dir>/.run.<job-id>.lock.

The mechanism (O_CREAT|O_EXCL create, dead-holder reclaim, in-process
threading.Lock guarding the reclaim TOCTOU window) lives in lib/proc_lock.py,
shared with install_sync's auto-apply lock. This module owns the per-job path
derivation and the InFlightLockHeld type.

This per-job lock is the sole overlap-prevention guarantee (§10): there is no
global sweep lock, so two sessions launching the same owed job is harmless -
only the lock winner runs.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cc_session_tools.lib.proc_lock import LockHeld, exclusive_lock, pid_alive
from cc_session_tools.lib.scheduler.store import scheduler_dir

__all__ = ["InFlightLockHeld", "in_flight_lock", "pid_alive"]


class InFlightLockHeld(LockHeld):
    """Raised when a job's in-flight lock is held by a live process."""


def _lock_path(job_id: str) -> Path:
    return scheduler_dir() / f".run.{job_id}.lock"


@contextmanager
def in_flight_lock(job_id: str) -> Iterator[None]:
    with exclusive_lock(
        _lock_path(job_id),
        held_exc=InFlightLockHeld,
        label=f"in-flight lock for {job_id!r}",
    ):
        yield
```

`pid_alive` is re-exported because `tests/scheduler/` and any scheduler-side caller may import it
from here; check with `grep -rn "from cc_session_tools.lib.scheduler.lock import" src tests` and
drop the re-export if nothing outside this module uses it. The three raised messages are
byte-identical to the previous wording, so `tests/scheduler/test_lock.py` needs no edit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_proc_lock.py tests/scheduler/ -v`
Expected: PASS, including `tests/scheduler/test_lock.py` **unchanged** — that is the extraction's
acceptance criterion (§Testing's last bullet). If you had to edit it, the extraction changed
behaviour; go back.

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add src/cc_session_tools/lib/proc_lock.py src/cc_session_tools/lib/scheduler/lock.py tests/test_proc_lock.py && git commit -m "refactor: hoist the O_EXCL process lock out of the scheduler

install_sync's forthcoming auto-apply needs the same exclusive, try-once,
dead-holder-reclaiming lock the scheduler already has for per-job
in-flight exclusion. Second user is the point at which it gets hoisted
rather than copied. scheduler/lock.py keeps its per-job path derivation
and its InFlightLockHeld type (now a LockHeld subclass) with identical
message wording, so its tests pass unchanged. The in-process thread guard
is now keyed on the lock path rather than the job id - strictly
finer-grained, since the path is what O_EXCL actually contends on."
```

---

### Task 3: Failure-attempt storage in `install_sync.py`

§5. A failed auto-apply must be remembered so it backs off instead of reprinting a failing
five-step install on every subsequent `ccst` invocation forever. Three rows in the **existing**
`install_sync` KV table — **no DDL change**, which is what keeps this a minor bump (§11).

**Files:**
- Modify: `src/cc_session_tools/lib/install_sync.py`
- Test: `tests/test_install_sync.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_install_sync.py`:

```python
# ---------- failed-attempt keys ----------

def test_get_failed_attempt_returns_none_when_never_recorded(db_path: Path) -> None:
    assert install_sync.get_failed_attempt(path=db_path) is None


def test_record_then_get_failed_attempt_round_trips(db_path: Path) -> None:
    install_sync.record_failed_attempt("2.5.0", rc=1, path=db_path)
    attempt = install_sync.get_failed_attempt(path=db_path)
    assert attempt is not None
    assert attempt.version == "2.5.0"
    assert attempt.rc == 1
    assert attempt.at.tzinfo is not None  # aware, so decide_auto_sync can subtract


def test_record_failed_attempt_overwrites_a_previous_one(db_path: Path) -> None:
    install_sync.record_failed_attempt("2.5.0", rc=1, path=db_path)
    install_sync.record_failed_attempt("2.6.0", rc=2, path=db_path)
    attempt = install_sync.get_failed_attempt(path=db_path)
    assert attempt is not None
    assert (attempt.version, attempt.rc) == ("2.6.0", 2)


def test_record_synced_clears_the_failure_keys(db_path: Path) -> None:
    """Spec section 5: cleared on ANY successful record_synced(), including
    the one an explicit `ccst install-everything --apply` writes - otherwise a
    user who fixes the broken step by hand still sees doctor FAIL forever."""
    install_sync.record_failed_attempt("2.5.0", rc=1, path=db_path)
    install_sync.record_synced("2.5.0", path=db_path)
    assert install_sync.get_failed_attempt(path=db_path) is None
    assert install_sync.get_synced_version(path=db_path) == "2.5.0"


def test_get_failed_attempt_on_nonexistent_db_returns_none(db_path: Path) -> None:
    assert install_sync.get_failed_attempt(path=db_path) is None
    assert not db_path.exists()


def test_get_failed_attempt_on_corrupt_db_returns_none(db_path: Path) -> None:
    """Same graceful degradation as get_synced_version: this is read on every
    non-exempt ccst invocation, so it must never be the thing that crashes a
    user's unrelated command."""
    db_path.write_bytes(b"this is not a sqlite database file")
    assert install_sync.get_failed_attempt(path=db_path) is None


def test_get_failed_attempt_with_an_unparseable_row_returns_none(db_path: Path) -> None:
    """The KV table is hand-editable with any sqlite3 shell, and a bad value
    would otherwise raise on every ccst invocation. Degrading to "no failed
    attempt" retries the apply, which is the safe direction."""
    install_sync.record_failed_attempt("2.5.0", rc=1, path=db_path)
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "UPDATE install_sync SET value = ? WHERE key = 'last_attempt_at'", ("not-a-timestamp",)
    )
    conn.commit()
    conn.close()

    assert install_sync.get_failed_attempt(path=db_path) is None
```

Add `from cc_session_tools.lib import sessions_db` to that test module's imports (the last test
needs it).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_sync.py -k "failed_attempt or clears_the_failure" -v`
Expected: FAIL — `AttributeError: module … has no attribute 'get_failed_attempt'`.

- [ ] **Step 3: Implement**

In `src/cc_session_tools/lib/install_sync.py`, add imports and the new surface:

```python
from dataclasses import dataclass
from datetime import datetime, timezone
```

```python
_LAST_ATTEMPT_VERSION_KEY = "last_attempt_version"
_LAST_ATTEMPT_AT_KEY = "last_attempt_at"
_LAST_ATTEMPT_RC_KEY = "last_attempt_rc"
_FAILURE_KEYS = (_LAST_ATTEMPT_VERSION_KEY, _LAST_ATTEMPT_AT_KEY, _LAST_ATTEMPT_RC_KEY)

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # matches sessions_db._now_iso()


@dataclass(frozen=True, slots=True)
class FailedAttempt:
    """A recorded auto-apply failure: which version was attempted, when, and
    the non-zero rc the five install steps produced."""

    version: str
    at: datetime
    rc: int


def get_failed_attempt(*, path: Path | None = None) -> FailedAttempt | None:
    """Return the last recorded failed auto-apply, or None if there isn't one.

    Read on every non-exempt ccst invocation, so it degrades to None rather
    than raising for every way the store can be unusable: no file, a
    pre-upgrade db with no install_sync table, a corrupt file (all
    sqlite3.DatabaseError, exactly as get_synced_version handles them), and a
    hand-edited row whose timestamp or rc doesn't parse. Degrading to "no
    failed attempt" means the next invocation retries the apply, which is the
    safe direction: at worst one extra 16.8 ms attempt.
    """
    try:
        conn = sessions_db.connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return None
    try:
        rows = conn.execute(
            "SELECT key, value FROM install_sync WHERE key IN (?, ?, ?)", _FAILURE_KEYS
        ).fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()

    values = {row["key"]: row["value"] for row in rows}
    if len(values) != len(_FAILURE_KEYS):
        return None
    try:
        at = datetime.strptime(values[_LAST_ATTEMPT_AT_KEY], _TS_FORMAT).replace(
            tzinfo=timezone.utc
        )
        rc = int(values[_LAST_ATTEMPT_RC_KEY])
    except ValueError:
        return None
    return FailedAttempt(version=values[_LAST_ATTEMPT_VERSION_KEY], at=at, rc=rc)


def record_failed_attempt(version: str, *, rc: int, path: Path | None = None) -> None:
    """Record that an auto-apply of `version` failed with `rc`, so
    decide_auto_sync can back off instead of retrying on every invocation."""
    now = sessions_db._now_iso()
    conn = sessions_db.connect(path=path)
    try:
        conn.executemany(
            "INSERT INTO install_sync (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            [
                (_LAST_ATTEMPT_VERSION_KEY, version, now),
                (_LAST_ATTEMPT_AT_KEY, now, now),
                (_LAST_ATTEMPT_RC_KEY, str(rc), now),
            ],
        )
        conn.commit()
    finally:
        conn.close()
```

And extend `record_synced` so success clears the failure state, in the same transaction:

```python
def record_synced(version: str, *, path: Path | None = None) -> None:
    """Record that `install-everything --apply` just succeeded for `version`,
    and clear any recorded failed auto-apply.

    Clearing here rather than in ensure_synced is deliberate: an explicit
    `ccst install-everything --apply` that succeeds must also clear the
    backoff, otherwise a user who fixes the broken step by hand keeps seeing
    doctor FAIL and the once-per-6h retry.
    """
    conn = sessions_db.connect(path=path)
    try:
        conn.execute(
            "INSERT INTO install_sync (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (_SYNCED_VERSION_KEY, version, sessions_db._now_iso()),
        )
        conn.execute(
            "DELETE FROM install_sync WHERE key IN (?, ?, ?)", _FAILURE_KEYS
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_sync.py -v`
Expected: PASS (whole file — confirms the existing marker tests still hold).

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add src/cc_session_tools/lib/install_sync.py tests/test_install_sync.py && git commit -m "feat(install-sync): remember a failed apply so it can back off

Three rows in the existing install_sync KV table - no DDL change, so no
migration is owed. An auto-apply that fails does so persistently (a real
~/.claude/skills/<name> directory blocking a symlink, unbalanced
CLAUDE.md sentinels); without a record it would reprint the same failing
five-step install on every subsequent ccst invocation forever. A
successful record_synced clears them in the same transaction, including
the one an explicit install-everything --apply writes."
```

---

### Task 4: `is_auto_sync_exempt()` — the pure argv+env decision

§1/§2. Split from `decide_auto_sync` on purpose: this must be answerable **from argv and the
environment alone**, so `ensure_synced` can short-circuit an exempt caller — above all
`ccst hooks run <verb>`, which fires on every tool call in every open session — **before any
marker read**. Kept separate from Task 7's wiring so every row of §2's table gets a direct unit
test with no `sys.argv` involved.

**Files:**
- Modify: `src/cc_session_tools/lib/install_sync.py`
- Test: `tests/test_install_sync.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_install_sync.py`:

```python
# ---------- is_auto_sync_exempt ----------

def test_hooks_run_is_exempt() -> None:
    """The hot path: fires on every tool call in every open Claude Code
    session. Must never apply (rewriting settings.json from inside a hook
    Claude Code invoked from settings.json is a race no atomic write makes
    tidy) and must never pay for the check."""
    assert install_sync.is_auto_sync_exempt(noun="hooks", verb="run", opted_out=False)


@pytest.mark.parametrize("noun", ["install-everything", "doctor", "repair", "migrate"])
def test_exempt_nouns(noun: str) -> None:
    """install-everything would recurse; doctor must stay able to REPORT the
    out-of-sync state rather than silently erasing it; repair and migrate are
    the recovery tools for a broken store and must run under any store state."""
    assert install_sync.is_auto_sync_exempt(noun=noun, verb=None, opted_out=False)


@pytest.mark.parametrize(
    "noun,verb",
    [
        ("skills", "install"),
        ("skills", "uninstall"),
        ("hooks", "install"),
        ("hooks", "uninstall"),
        ("shell", "install"),
        ("claude-md", "install"),
        ("ccsched-jobs", "install"),
    ],
)
def test_install_and_uninstall_verbs_are_exempt(noun: str, verb: str) -> None:
    """The user is driving install state by hand. Running a full
    default-target auto-apply underneath them is both surprising and
    self-contradictory - `ccst skills install --target /tmp/x` must not also
    symlink into ~/.claude/skills."""
    assert install_sync.is_auto_sync_exempt(noun=noun, verb=verb, opted_out=False)


def test_env_opt_out_exempts_everything() -> None:
    assert install_sync.is_auto_sync_exempt(noun="pdata", verb="list", opted_out=True)


@pytest.mark.parametrize(
    "noun,verb",
    [("pdata", "list"), ("sessions", "list"), ("telemetry", "trim"), ("gc", "report")],
)
def test_carriers_are_not_exempt(noun: str, verb: str) -> None:
    assert not install_sync.is_auto_sync_exempt(noun=noun, verb=verb, opted_out=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_sync.py -k auto_sync_exempt -v`
Expected: FAIL — `AttributeError: … has no attribute 'is_auto_sync_exempt'`.

Note: the existing `_EXEMPT_NOUNS` and `should_block_for_unsynced_install` are still present and
still called by `main()` at this point. They are removed in Task 8, once the replacement is wired.
Do not remove them here — that would leave `main()` calling a function that no longer exists.

- [ ] **Step 3: Implement**

Add to `src/cc_session_tools/lib/install_sync.py`, next to the existing `_EXEMPT_NOUNS`:

```python
_EXEMPT_VERBS = frozenset({"install", "uninstall"})

AUTO_SYNC_OPT_OUT_ENV = "CCST_NO_AUTO_SYNC"


def is_auto_sync_exempt(*, noun: str | None, verb: str | None, opted_out: bool) -> bool:
    """True if this invocation must not trigger an auto-apply.

    Pure, argv-and-env only - deliberately answerable without touching
    sessions.db, so ensure_synced can short-circuit an exempt caller before
    any marker read. `ccst hooks run <verb>` is the reason that property
    matters: it fires on every tool call in every open Claude Code session,
    and the 0.56 ms marker read is a cost measured per invocation, not per
    command.

    Exemptions, in order: the env opt-out (CI, bisecting, and this repo's own
    test suite); `hooks run`, which must never rewrite settings.json from
    inside a hook Claude Code invoked from settings.json mid-session;
    install-everything (would recurse), doctor (must be able to report the
    out-of-sync state rather than silently erasing it), and repair/migrate
    (the recovery tools for a broken store, which must run under any store
    state); and any `install`/`uninstall` verb, where the user is driving
    install state by hand with their own --target/--source/--hook and a
    default-target auto-apply underneath them would be self-contradictory.
    """
    if opted_out:
        return True
    if noun == "hooks" and verb == "run":
        return True
    if noun in _EXEMPT_NOUNS:
        return True
    return verb in _EXEMPT_VERBS
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add src/cc_session_tools/lib/install_sync.py tests/test_install_sync.py && git commit -m "feat(install-sync): add is_auto_sync_exempt, the pure argv+env gate

Answerable without opening sessions.db, which is the whole point: 'ccst
hooks run <verb>' fires on every tool call in every open session and must
short-circuit before the marker read, not after it. Adds the
install/uninstall verb exemption - a user driving install state by hand
with their own --target must not get a default-target apply underneath
them - and CCST_NO_AUTO_SYNC as the escape hatch. Not wired into main()
yet; the old gate is still in place until its replacement is complete."
```

---

### Task 5: `AutoSyncAction` + `decide_auto_sync()` — the pure marker decision

§1/§5. Only ever reached with markers already in hand.

**Files:**
- Modify: `src/cc_session_tools/lib/install_sync.py`
- Test: `tests/test_install_sync.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_install_sync.py` (and `from datetime import datetime, timedelta, timezone` to
its imports):

```python
# ---------- decide_auto_sync ----------

_NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


def test_synced_skips() -> None:
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.5.0", last_failure=None, now=_NOW
    ) is install_sync.AutoSyncAction.SKIP_SYNCED


def test_stale_applies() -> None:
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.4.0", last_failure=None, now=_NOW
    ) is install_sync.AutoSyncAction.APPLY


def test_never_synced_applies() -> None:
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version=None, last_failure=None, now=_NOW
    ) is install_sync.AutoSyncAction.APPLY


def test_recent_failure_for_this_version_backs_off() -> None:
    failure = install_sync.FailedAttempt(
        version="2.5.0", at=_NOW - timedelta(hours=1), rc=1
    )
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.4.0", last_failure=failure, now=_NOW
    ) is install_sync.AutoSyncAction.SKIP_BACKOFF


def test_failure_outside_the_window_retries_once() -> None:
    failure = install_sync.FailedAttempt(
        version="2.5.0", at=_NOW - timedelta(hours=7), rc=1
    )
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.4.0", last_failure=failure, now=_NOW
    ) is install_sync.AutoSyncAction.APPLY


def test_failure_for_a_different_version_does_not_back_off() -> None:
    """The key is version-scoped on purpose: a new release may well fix the
    failing step, so installing a different version resets the backoff."""
    failure = install_sync.FailedAttempt(
        version="2.4.0", at=_NOW - timedelta(minutes=5), rc=1
    )
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.4.0", last_failure=failure, now=_NOW
    ) is install_sync.AutoSyncAction.APPLY


def test_synced_wins_over_a_stale_failure_record() -> None:
    """Belt-and-braces on the check order: if the marker says we're synced,
    a leftover failure row must not produce a spurious backoff warning."""
    failure = install_sync.FailedAttempt(
        version="2.5.0", at=_NOW - timedelta(minutes=5), rc=1
    )
    assert install_sync.decide_auto_sync(
        installed_version="2.5.0", synced_version="2.5.0", last_failure=failure, now=_NOW
    ) is install_sync.AutoSyncAction.SKIP_SYNCED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_sync.py -k decide_auto_sync -v`
Expected: FAIL — `AttributeError: … has no attribute 'AutoSyncAction'`.

- [ ] **Step 3: Implement**

Add to `src/cc_session_tools/lib/install_sync.py` (plus `from datetime import timedelta` and
`from enum import Enum` to its imports):

```python
FAILURE_BACKOFF = timedelta(hours=6)


class AutoSyncAction(str, Enum):
    SKIP_SYNCED = "skip-synced"
    SKIP_BACKOFF = "skip-backoff"
    APPLY = "apply"


def decide_auto_sync(
    *,
    installed_version: str,
    synced_version: str | None,
    last_failure: FailedAttempt | None,
    now: datetime,
) -> AutoSyncAction:
    """What ensure_synced should do, given the markers.

    Pure - no I/O, `now` injected - so every branch is directly unit-testable.

    SKIP_BACKOFF exists because the two reachable failure modes
    (_cmd_skills_install refusing a real ~/.claude/skills/<name> directory
    without --force, _cmd_claude_md_install raising MalformedBlockError on
    unbalanced sentinels) are persistent: they fail identically on every retry
    until a human intervenes. Without the window, an unasked-for side effect
    would reprint a failing five-step install on every ccst invocation. The
    failure record is version-scoped, so installing a different version resets
    it - a new release may well fix the failing step.
    """
    if synced_version == installed_version:
        return AutoSyncAction.SKIP_SYNCED
    if (
        last_failure is not None
        and last_failure.version == installed_version
        and now - last_failure.at < FAILURE_BACKOFF
    ):
        return AutoSyncAction.SKIP_BACKOFF
    return AutoSyncAction.APPLY
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add src/cc_session_tools/lib/install_sync.py tests/test_install_sync.py && git commit -m "feat(install-sync): add decide_auto_sync and AutoSyncAction

Pure, now injected, so the 6h backoff window and the version-scoping of
the failure record both get direct tests. Backoff is what stops an
unasked-for side effect turning a persistent install failure into a
failing five-step install reprinted on every ccst invocation forever."
```

---

### Task 6: `run_install_everything()` core + `_cmd_install_everything` adapter

§4. Auto-apply must reuse the same five steps rather than duplicating the step list, must not pay
the ~1.55 s trailing health check (which takes a network dependency on PyPI), and must not write
to stdout.

**Spec gap 1:** §4's signature takes `stream: TextIO`, but the five step functions print directly
to stdout and take no stream. **Call: `contextlib.redirect_stdout(stream)` around the step loop.**
`stream=sys.stdout` is then a no-op passthrough for the adapter path, `stream=io.StringIO()`
captures everything for auto-apply, and none of the five functions change. Diagnosis point 5
records why this is sufficient: their only `file=sys.stderr` writes are on paths that also return
rc 1, so a successful auto-apply emits nothing but §4's two lines.

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py:1439-1544`
- Test: `tests/test_ccst_install_everything.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ccst_install_everything.py`:

```python
# ---------- run_install_everything core ----------


def test_health_check_false_does_not_run_doctor(tmp_path: Path, mocker: MockerFixture) -> None:
    """Guards against auto-apply silently acquiring doctor's ~1.5 s PyPI
    dependency. The five install steps are 16.8 ms; the trailing health check
    is 1.55 s of it, dominated by a network call. Auto-apply is not a
    diagnostic run and must not pay that on someone else's command."""
    import io

    from cc_session_tools.cli import ccst

    doctor = mocker.patch.object(ccst, "_cmd_doctor", return_value=0)
    buf = io.StringIO()

    rc = ccst.run_install_everything(
        apply=False,
        stream=buf,
        health_check=False,
        skills_target=str(tmp_path / "skills"),
        hooks_target=str(tmp_path / "settings.json"),
        fragments_dir=str(tmp_path / "shellrc.d"),
        claude_md_target=str(tmp_path / "CLAUDE.md"),
    )

    assert rc == 0
    doctor.assert_not_called()


def test_health_check_true_runs_doctor(tmp_path: Path, mocker: MockerFixture) -> None:
    """The _cmd_install_everything adapter path keeps the health check."""
    import io

    from cc_session_tools.cli import ccst

    doctor = mocker.patch.object(ccst, "_cmd_doctor", return_value=0)
    buf = io.StringIO()

    ccst.run_install_everything(
        apply=False,
        stream=buf,
        health_check=True,
        no_pypi=True,
        skills_target=str(tmp_path / "skills"),
        hooks_target=str(tmp_path / "settings.json"),
        fragments_dir=str(tmp_path / "shellrc.d"),
        claude_md_target=str(tmp_path / "CLAUDE.md"),
    )

    doctor.assert_called_once()


def test_step_output_goes_to_the_stream_not_stdout(
    tmp_path: Path, mocker: MockerFixture, capsys
) -> None:
    """Section 4's hard requirement: `ccst sessions list --json` emits
    machine-readable stdout, and scheduler/worker.py carries a job's stdout
    into the ledger as its recorded findings. Interleaving install chatter
    there is a correctness bug, not a cosmetic one."""
    import io

    from cc_session_tools.cli import ccst

    mocker.patch.object(ccst, "_cmd_doctor", return_value=0)
    buf = io.StringIO()

    ccst.run_install_everything(
        apply=False,
        stream=buf,
        health_check=False,
        skills_target=str(tmp_path / "skills"),
        hooks_target=str(tmp_path / "settings.json"),
        fragments_dir=str(tmp_path / "shellrc.d"),
        claude_md_target=str(tmp_path / "CLAUDE.md"),
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Skills" in buf.getvalue()
    assert "Scheduled jobs" in buf.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_install_everything.py -k "health_check or step_output" -v`
Expected: FAIL — `AttributeError: module 'cc_session_tools.cli.ccst' has no attribute
'run_install_everything'`.

- [ ] **Step 3: Split the command into core + adapter**

Add `import contextlib` and `from typing import Any, TextIO, cast` to `ccst.py`'s imports
(`ccst.py:65-73` — `typing` currently imports `Any, cast`).

Replace `_cmd_install_everything` (`ccst.py:1439-1544`) with:

```python
def run_install_everything(
    *,
    apply: bool,
    stream: TextIO,
    health_check: bool,
    no_pypi: bool = False,
    skills_target: str | None = None,
    hooks_target: str | None = None,
    fragments_dir: str | None = None,
    claude_md_target: str | None = None,
) -> int:
    """Run the five install steps, optionally followed by a health check.

    Returns the worst rc the five steps produced; the health check's own rc is
    deliberately not part of it, matching the diagnostic-not-gate role it has
    always had here.

    `stream` receives every step's progress output. The five step functions
    print to stdout directly, so the loop runs under
    contextlib.redirect_stdout(stream): the CLI adapter passes sys.stdout (a
    no-op passthrough) and install_sync.ensure_synced passes a StringIO it
    discards on success. Auto-apply must not write to stdout at all -
    `ccst sessions list --json` emits machine-readable stdout, and
    scheduler/worker.py carries a job's stdout into the ledger as its recorded
    findings.

    `health_check=False` is what auto-apply uses: the five steps are 16.8 ms,
    the trailing `ccst doctor` is ~1.55 s dominated by its PyPI network call.
    An auto-apply is not a diagnostic run and must not take a network
    dependency on someone else's command.
    """
    hooks_target = hooks_target or str(Path.home() / ".claude" / "settings.json")

    steps: list[tuple[str, str, object]] = [
        (
            "Skills",
            "skills",
            argparse.Namespace(source=None, target=skills_target, apply=apply, force=False),
        ),
        (
            "Hooks",
            "hooks",
            argparse.Namespace(
                source=None,
                hook=None,
                target=hooks_target,
                apply=apply,
            ),
        ),
        (
            "Shell helpers",
            "shell",
            argparse.Namespace(
                apply=apply,
                fragments_dir=[fragments_dir] if fragments_dir else None,
            ),
        ),
        (
            "Global CLAUDE.md",
            "claude-md",
            argparse.Namespace(target=claude_md_target, apply=apply),
        ),
        (
            "Scheduled jobs",
            "ccsched-jobs",
            argparse.Namespace(apply=apply),
        ),
    ]

    dispatch: dict[str, object] = {
        "skills": _cmd_skills_install,
        "hooks": _cmd_hooks_install,
        "shell": _cmd_shell_install,
        "claude-md": _cmd_claude_md_install,
        "ccsched-jobs": _cmd_ccsched_jobs_install,
    }

    # +1 accounts for the trailing health check, which isn't itself a `steps` entry.
    total_steps = len(steps) + (1 if health_check else 0)
    overall_rc = 0
    with contextlib.redirect_stdout(stream):
        for i, (label, key, step_args) in enumerate(steps, start=1):
            print(f"\n=== {i}/{total_steps}  {label} ===")
            rc = dispatch[key](step_args)  # type: ignore[operator]
            if rc != 0:
                overall_rc = rc

        if apply and overall_rc == 0:
            import sqlite3

            from cc_session_tools.lib import install_sync
            try:
                install_sync.record_synced(__version__)
            except sqlite3.DatabaseError as exc:
                # A corrupt sessions.db must not erase the five install steps'
                # already-successful work (and their printed summary) that ran
                # just above - print a clear warning and continue. Letting this
                # propagate would also be self-defeating: this is the exact
                # command the out-of-sync warning tells a user to run.
                print(
                    f"  warning: could not record the install-everything sync marker "
                    f"({exc}) - sessions.db may be corrupt; run `ccst repair sessions` "
                    "to investigate",
                    file=sys.stderr,
                )

        if health_check:
            print(f"\n=== {total_steps}/{total_steps}  Health check ===")
            _cmd_doctor(
                argparse.Namespace(
                    settings=None,
                    skills_dir=None,
                    no_pypi=no_pypi,
                    drift=False,
                    mute=None,
                    unmute=None,
                    list_mutes=False,
                    mutes_file=None,
                    mode=None,
                )
            )

        if not apply:
            print("\nDry run complete — re-run with --apply to write all changes.")
        else:
            print("\nAll install steps complete.")
    return overall_rc


def _cmd_install_everything(args: argparse.Namespace) -> int:
    """argparse adapter for run_install_everything: the interactive CLI path,
    which streams to stdout and always ends with the health check."""
    return run_install_everything(
        apply=args.apply,
        stream=sys.stdout,
        health_check=True,
        no_pypi=args.no_pypi,
        skills_target=getattr(args, "skills_target", None),
        hooks_target=getattr(args, "hooks_target", None),
        fragments_dir=getattr(args, "fragments_dir", None),
        claude_md_target=getattr(args, "claude_md_target", None),
    )
```

Note the `record_synced` block stays inside `run_install_everything`, not in the adapter: a
successful auto-apply must advance the marker too, and clearing the failure keys (Task 3) rides on
the same call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_install_everything.py -v`
Expected: PASS — including `test_section_headers_present` and `test_apply_flag_accepted`, which
exercise the adapter path unchanged (`health_check=True`, so the `N/6` labels are as before).

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add src/cc_session_tools/cli/ccst.py tests/test_ccst_install_everything.py && git commit -m "refactor(install-everything): split into a parameterised core plus adapter

The forthcoming auto-apply needs the same five steps with two differences:
no trailing health check (1.55 s and a PyPI network call, versus 16.8 ms
for the steps themselves) and no stdout writes (ccst sessions list --json
is machine-readable, and scheduler/worker.py files a job's stdout as its
ledger findings). Both are parameters now rather than a second copy of the
step list. The five step functions print to stdout directly, so the core
redirects stdout into the caller's stream - sys.stdout for the CLI, a
discarded buffer for auto-apply."
```

---

### Task 7: `ensure_synced()` — the impure wrapper

§1, §3, §4, §5. Order is load-bearing: exempt check first (no marker read at all), then markers,
then `decide_auto_sync`, then on `APPLY` the lock, the apply, and the outcome record. It never
raises to its caller and never refuses to dispatch.

**Spec gap 2:** §5 asks for "an in-process flag so a single `ccst` process never retries", but
`main()` calls this once per process. **Call: implement it, with its own test.** It is a real
observable behaviour of the function (not dead code), and it is what makes `ensure_synced` safe to
call from a second site later.

**Spec gap 3:** §5 does not say what happens when `record_failed_attempt` itself raises on a
corrupt store. **Call: catch `sqlite3.DatabaseError` and print the same `ccst repair sessions`
pointer**, because §5's stated invariant is that auto-apply is *never fatal* — killing the user's
unrelated `ccst pdata list` because the failure bookkeeping failed would violate it. This is not a
log-and-continue with no recovery: the recovery is "the requested command still runs", which is the
product requirement, and it has a test.

**Files:**
- Modify: `src/cc_session_tools/lib/install_sync.py`
- Test: `tests/test_install_sync.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_install_sync.py`. These need a real sandboxed environment — HOME, both store
roots, and the opt-out removed:

```python
# ---------- ensure_synced (end-to-end, sandboxed) ----------

@pytest.fixture
def auto_sync_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Everything ensure_synced touches, redirected under tmp_path, and the
    suite-wide CCST_NO_AUTO_SYNC opt-out removed so the real code path runs.
    Returns the sandboxed HOME."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "scheduler"))
    monkeypatch.delenv("CCST_NO_AUTO_SYNC", raising=False)
    monkeypatch.setattr(install_sync, "_auto_sync_attempted", False)
    return home


def test_exempt_caller_never_opens_sessions_db(
    auto_sync_env: Path, mocker: MockerFixture
) -> None:
    """The property section 2's hot-path exemption depends on: `ccst hooks run
    <verb>` fires on every tool call in every open session and must not pay
    the 0.56 ms marker read, let alone the apply."""
    spy = mocker.patch.object(install_sync, "get_synced_version")

    install_sync.ensure_synced(noun="hooks", verb="run", installed_version="2.5.0")

    spy.assert_not_called()


def test_env_opt_out_never_opens_sessions_db(
    auto_sync_env: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    monkeypatch.setenv("CCST_NO_AUTO_SYNC", "1")
    spy = mocker.patch.object(install_sync, "get_synced_version")

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    spy.assert_not_called()


def test_applies_and_advances_the_marker(auto_sync_env: Path, capsys) -> None:
    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    assert install_sync.get_synced_version() == "2.5.0"
    captured = capsys.readouterr()
    assert captured.out == ""  # section 4: never stdout
    assert "out of sync" in captured.err
    assert "synced to 2.5.0" in captured.err


def test_already_synced_prints_nothing_and_does_not_apply(
    auto_sync_env: Path, mocker: MockerFixture, capsys
) -> None:
    install_sync.record_synced("2.5.0")
    capsys.readouterr()
    from cc_session_tools.cli import ccst

    runner = mocker.patch.object(ccst, "run_install_everything", return_value=0)

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    runner.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_failed_apply_records_all_three_keys_and_does_not_advance_the_marker(
    auto_sync_env: Path, mocker: MockerFixture, capsys
) -> None:
    from cc_session_tools.cli import ccst

    mocker.patch.object(ccst, "run_install_everything", return_value=1)

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    assert install_sync.get_synced_version() is None
    attempt = install_sync.get_failed_attempt()
    assert attempt is not None
    assert (attempt.version, attempt.rc) == ("2.5.0", 1)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "install-everything --apply" in captured.err


def test_failed_apply_emits_the_buffered_step_output_on_stderr(
    auto_sync_env: Path, mocker: MockerFixture, capsys
) -> None:
    """Section 4: on failure the buffered step output is emitted verbatim, so
    a user can see WHICH step failed without re-running anything."""
    from cc_session_tools.cli import ccst

    def failing(**kwargs: object) -> int:
        stream = kwargs["stream"]
        stream.write("=== 1/5  Skills ===\nerror: something specific\n")  # type: ignore[union-attr]
        return 1

    mocker.patch.object(ccst, "run_install_everything", side_effect=failing)

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: something specific" in captured.err


def test_backoff_prints_one_line_and_does_not_apply(
    auto_sync_env: Path, mocker: MockerFixture, capsys
) -> None:
    install_sync.record_failed_attempt("2.5.0", rc=1)
    capsys.readouterr()
    from cc_session_tools.cli import ccst

    runner = mocker.patch.object(ccst, "run_install_everything", return_value=0)

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    runner.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert len([line for line in captured.err.splitlines() if line.strip()]) == 1
    assert "last auto-sync failed" in captured.err


def test_successful_apply_clears_a_stale_failure_record(
    auto_sync_env: Path, capsys
) -> None:
    install_sync.record_failed_attempt("2.4.0", rc=1)  # different version, no backoff

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    assert install_sync.get_synced_version() == "2.5.0"
    assert install_sync.get_failed_attempt() is None


def test_lock_contention_skips_the_apply_silently(
    auto_sync_env: Path, tmp_path: Path, mocker: MockerFixture, capsys
) -> None:
    """Try-once, do not wait: blocking an unrelated CLI call on a lock would
    make auto-apply a latency hazard, and skipping is harmless - the winner is
    applying the same thing."""
    import json
    import os

    lock = tmp_path / "data-home" / ".install-sync.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "started": "x"}))  # live pid
    from cc_session_tools.cli import ccst

    runner = mocker.patch.object(ccst, "run_install_everything", return_value=0)

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    runner.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_dead_pid_lock_is_reclaimed_and_the_apply_proceeds(
    auto_sync_env: Path, tmp_path: Path
) -> None:
    import json

    lock = tmp_path / "data-home" / ".install-sync.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 2_000_000_000, "started": "x"}))

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    assert install_sync.get_synced_version() == "2.5.0"


def test_second_call_in_the_same_process_does_not_reapply(
    auto_sync_env: Path, mocker: MockerFixture
) -> None:
    """Section 5's degenerate case: when the marker store itself is unwritable,
    nothing the first attempt records survives, so the decision comes out
    APPLY again. A single ccst process must never retry.

    record_failed_attempt is stubbed out precisely so the backoff can't be
    what makes this pass - without the stub, the second call would take the
    SKIP_BACKOFF branch and the in-process flag would be untested."""
    from cc_session_tools.cli import ccst

    runner = mocker.patch.object(ccst, "run_install_everything", return_value=1)
    mocker.patch.object(install_sync, "record_failed_attempt")

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")
    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    assert runner.call_count == 1
    assert install_sync.get_failed_attempt() is None  # the stub really did no-op


def test_never_raises_when_the_failure_record_cannot_be_written(
    auto_sync_env: Path, tmp_path: Path, mocker: MockerFixture, capsys
) -> None:
    """Auto-apply is a side effect, never fatal: if even the bookkeeping
    fails, the caller's command must still run."""
    from cc_session_tools.cli import ccst

    mocker.patch.object(ccst, "run_install_everything", return_value=1)
    mocker.patch.object(
        install_sync, "record_failed_attempt", side_effect=sqlite3.DatabaseError("corrupt")
    )

    install_sync.ensure_synced(noun="pdata", verb="list", installed_version="2.5.0")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ccst repair sessions" in captured.err
```

Add `import sqlite3` and `from pytest_mock import MockerFixture` to the test module's imports.

**On the one `raise` in the implementation below:** the `SKIP_BACKOFF` branch needs
`last_failure`, which `decide_auto_sync` can only have returned `SKIP_BACKOFF` for if it was not
None — but the type system can't see that. Per this repo's standard, the choice is "fix the type
upstream or throw with a clear message so the impossible state becomes visible"; a narrower return
type from `decide_auto_sync` would mean a tagged union for one call site, so this uses the throw.
It has no test because it is genuinely unreachable, which is the point of it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_install_sync.py -k ensure_synced -v`
Expected: FAIL — `AttributeError: … has no attribute 'ensure_synced'`.

- [ ] **Step 3: Implement**

Add to `src/cc_session_tools/lib/install_sync.py` (plus `import io`, `import os`, `import sys`):

```python
# Set once an apply has been attempted in this process. The degenerate case
# section 5 describes - a sessions.db so broken that neither the sync marker
# nor the failure keys can be written - would otherwise re-decide APPLY every
# time ensure_synced is called, since nothing on disk changed.
_auto_sync_attempted = False


def ensure_synced(*, noun: str | None, verb: str | None, installed_version: str) -> None:
    """Bring ~/.claude's registration into line with the installed version,
    then return so the requested command can run.

    Called from ccst.main() before dispatch, for every non-exempt invocation.
    Three invariants, in order of how badly breaking them would hurt:

    1. It never raises and never refuses to dispatch. Auto-apply is a side
       effect, not the command.
    2. It never changes the invocation's exit code. ccsched's ledger
       auto-suspends a job after 10 consecutive failures; an install failure
       leaking into $? would start suspending healthy jobs.
    3. It never writes to stdout. `ccst sessions list --json` is
       machine-readable, and scheduler/worker.py files a job's stdout as its
       recorded findings.

    An exempt caller returns before any marker read - see is_auto_sync_exempt.
    A contender that finds a live lock holder skips silently rather than
    waiting: the winner is applying the same thing, and blocking an unrelated
    CLI call on a lock would make this a latency hazard.
    """
    global _auto_sync_attempted

    if is_auto_sync_exempt(
        noun=noun,
        verb=verb,
        opted_out=os.environ.get(AUTO_SYNC_OPT_OUT_ENV) == "1",
    ):
        return
    if _auto_sync_attempted:
        return

    synced_version = get_synced_version()
    last_failure = get_failed_attempt()
    action = decide_auto_sync(
        installed_version=installed_version,
        synced_version=synced_version,
        last_failure=last_failure,
        now=datetime.now(timezone.utc),
    )
    if action is AutoSyncAction.SKIP_SYNCED:
        return

    state = "never synced" if synced_version is None else f"last synced {synced_version}"

    if action is AutoSyncAction.SKIP_BACKOFF:
        if last_failure is None:
            raise AssertionError(
                "decide_auto_sync returned SKIP_BACKOFF without a failed attempt"
            )
        print(
            f"ccst: install config is out of sync (installed {installed_version}, {state}) "
            f"and the last auto-sync failed at {last_failure.at.strftime(_TS_FORMAT)} — "
            "run `ccst install-everything --apply` to see why.",
            file=sys.stderr,
        )
        return

    _auto_sync_attempted = True

    # Function-local imports: ccst.py already imports this module, and this is
    # the lazy-import convention ccst.py itself uses throughout (_cmd_doctor,
    # _cmd_install_everything, _cmd_ccsched_jobs_install all import their
    # dependencies inside the function body).
    from cc_session_tools.cli.ccst import run_install_everything
    from cc_session_tools.lib.paths import data_home
    from cc_session_tools.lib.proc_lock import LockHeld, exclusive_lock

    buffer = io.StringIO()
    try:
        with exclusive_lock(data_home() / ".install-sync.lock"):
            print(
                f"ccst: install config is out of sync (installed {installed_version}, "
                f"{state}) — syncing…",
                file=sys.stderr,
            )
            rc = run_install_everything(apply=True, stream=buffer, health_check=False)
    except LockHeld:
        return

    if rc == 0:
        print(f"ccst: install config synced to {installed_version}.", file=sys.stderr)
        return

    # Failure: the buffered per-step detail is the only place the reason
    # appears, so it goes out verbatim rather than being discarded.
    sys.stderr.write(buffer.getvalue())
    print(
        f"ccst: install config auto-sync failed (rc {rc}) — "
        "run `ccst install-everything --apply` to see why.",
        file=sys.stderr,
    )
    try:
        record_failed_attempt(installed_version, rc=rc)
    except sqlite3.DatabaseError as exc:
        # Never fatal: the caller's command must still run even when the
        # backoff bookkeeping itself can't be written. Backoff can't help when
        # the backoff store is the broken thing, so this invocation warns and
        # every subsequent process re-runs the 16.8 ms apply until the store is
        # repaired. `ccst repair sessions` is exempt, so the fix stays reachable.
        print(
            f"  warning: could not record the failed auto-sync ({exc}) - sessions.db "
            "may be corrupt; run `ccst repair sessions` to investigate",
            file=sys.stderr,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_install_sync.py -v && uv run pytest -q`
Expected: PASS. `ensure_synced` is still not called from anywhere in production at this point —
`main()` is wired in Task 8.

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add src/cc_session_tools/lib/install_sync.py tests/test_install_sync.py && git commit -m "feat(install-sync): add ensure_synced, the auto-apply executor

Exempt check first (so hooks run never opens sessions.db), then the
markers, then decide_auto_sync; on APPLY, an exclusive try-once lock, the
five steps with the health check off, and the outcome recorded. Never
raises, never touches stdout, never changes the caller's exit code - the
three invariants that let this be a side effect of an unrelated command.
Lock contention skips silently: the winner is applying the same thing.
Not wired into main() yet."
```

---

### Task 8: `main()` calls `ensure_synced`; delete the old gate and its tests

§2. Same call site the 2.4.0 gate occupies: immediately after `parser.parse_args()` and the
`args.noun is None` usage check. Three consequences of that placement, all wanted: an invalid
command line still fails at argparse without triggering an apply; `ccst --version`/`--help`/bare
`ccst` never trigger one; and a successful apply is in place *before* the requested command runs.

`should_block_for_unsynced_install` is deleted outright, not deprecated — one production caller
(the code being replaced here) plus its tests. Doing the swap and the deletion in one task is what
keeps the tree from ever having a `main()` that calls a function that no longer exists.

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py:2217-2254`
- Modify: `src/cc_session_tools/lib/install_sync.py` (delete `should_block_for_unsynced_install`)
- Modify: `tests/test_install_sync.py` (delete the `should_block_*` block, lines 71-155 of the
  current file)
- Rewrite: `tests/test_ccst_main_gate.py`

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_ccst_main_gate.py` entirely:

```python
"""Tests for the install-sync auto-apply call site in ccst.cli.ccst.main().

Task-level counterpart to tests/test_install_sync.py: those prove the decision
functions and the executor are right in isolation; these prove main() calls
ensure_synced with the right noun/verb, at the right point, and dispatches the
requested command regardless of what auto-sync did.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from cc_session_tools.cli import ccst


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    return tmp_path


def test_main_calls_ensure_synced_with_the_parsed_noun_and_verb(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cc_session_tools.lib import install_sync

    ensure = mocker.patch.object(install_sync, "ensure_synced")
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch.object(ccst, "_cmd_skills_install", return_value=0)

    with pytest.raises(SystemExit):
        ccst.main()

    ensure.assert_called_once_with(
        noun="skills", verb="install", installed_version=ccst.__version__
    )


def test_main_dispatches_even_when_auto_sync_applies(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """The whole point of replacing the 2.4.0 gate: a stale install no longer
    refuses to run the requested command."""
    from cc_session_tools.lib import install_sync

    mocker.patch.object(install_sync, "ensure_synced")
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    dispatched = mocker.patch.object(ccst, "_cmd_skills_install", return_value=0)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 0
    dispatched.assert_called_once()


@pytest.mark.parametrize("rc", [0, 1, 3])
def test_exit_code_is_the_commands_own_regardless_of_auto_sync(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture, rc: int
) -> None:
    """ccsched's ledger auto-suspends a job after 10 consecutive failures. An
    install-sync failure leaking into $? would start suspending healthy jobs."""
    from cc_session_tools.lib import install_sync

    mocker.patch.object(install_sync, "ensure_synced")
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch.object(ccst, "_cmd_skills_install", return_value=rc)

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == rc


def test_ensure_synced_runs_before_the_command_dispatches(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """Ordering matters: a hook or skill registered by the apply must be in
    place before the requested command runs, not after."""
    from cc_session_tools.lib import install_sync

    order: list[str] = []
    mocker.patch.object(
        install_sync, "ensure_synced", side_effect=lambda **kw: order.append("sync")
    )
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "skills", "install"])
    mocker.patch.object(
        ccst, "_cmd_skills_install", side_effect=lambda a: (order.append("cmd"), 0)[1]
    )

    with pytest.raises(SystemExit):
        ccst.main()

    assert order == ["sync", "cmd"]


def test_bare_ccst_exits_on_usage_without_calling_ensure_synced(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """The usage check runs first, so `ccst` with no arguments never triggers
    an apply."""
    from cc_session_tools.lib import install_sync

    ensure = mocker.patch.object(install_sync, "ensure_synced")
    monkeypatch.setattr(ccst.sys, "argv", ["ccst"])

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 1
    ensure.assert_not_called()


def test_should_block_for_unsynced_install_is_gone() -> None:
    """Deleted outright rather than deprecated - it had one production caller
    (this call site) and its own tests, and 'deleting code is a feature'."""
    from cc_session_tools.lib import install_sync

    assert not hasattr(install_sync, "should_block_for_unsynced_install")
```

Also delete from `tests/test_install_sync.py` the whole
`# ---------- should_block_for_unsynced_install ----------` section and the nine tests under it
(current lines 71-155). Every property they asserted is now covered by Task 4's
`is_auto_sync_exempt` tests (each exempt noun, `hooks run`) and Task 5's `decide_auto_sync` tests
(matched / mismatched / never-synced); the `is_interactive` cases have no successor because
`is_interactive` is gone.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_main_gate.py -v`
Expected: FAIL — `ensure.assert_called_once_with(...)` fails (nothing calls it) and
`test_should_block_for_unsynced_install_is_gone` fails (it is still there).

- [ ] **Step 3: Wire `main()` and delete the old function**

In `src/cc_session_tools/cli/ccst.py`, replace `main()`'s gate block (`ccst.py:2225-2254`) with:

```python
    from cc_session_tools.lib import install_sync

    # Bring ~/.claude's registration into line with the installed version
    # before dispatching. What goes stale on an upgrade is registration, not
    # code: skills symlinks and `ccst hooks run`'s dynamic import both resolve
    # into the uv tools path that --reinstall reuses in place, but a hook or
    # skill added in a newer version isn't wired into settings.json /
    # ~/.claude/skills until install-everything runs, so it silently never
    # fires. Wheels have no post-install hook, so the first ccst process after
    # an upgrade is the earliest possible moment.
    #
    # Cost on a carrier invocation is one readonly SQLite open/read/close,
    # 0.56 ms against a 94 ms bare process start. `ccst hooks run <verb>` is
    # exempt and doesn't pay even that - not for the per-call cost but for the
    # invocation count (every tool call in every open session) and because
    # rewriting settings.json from inside a hook Claude Code invoked from
    # settings.json is a race no atomic write makes tidy. See
    # install_sync.is_auto_sync_exempt for the full exempt set.
    #
    # Placement is after parse_args and after the usage check on purpose: an
    # invalid command line still fails at argparse without triggering an
    # apply, --version/--help/bare ccst never trigger one, and a successful
    # apply is in place before the requested command runs.
    install_sync.ensure_synced(
        noun=args.noun,
        verb=getattr(args, "verb", None),
        installed_version=__version__,
    )
```

Delete `should_block_for_unsynced_install` and its docstring from
`src/cc_session_tools/lib/install_sync.py` (current lines 57-89). Keep `_EXEMPT_NOUNS` — Task 4's
`is_auto_sync_exempt` uses it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_main_gate.py tests/test_install_sync.py -v`
Expected: PASS.

Then the whole suite, because this changes every `ccst` invocation's entrypoint:

Run: `uv run pytest -q`
Expected: PASS. If a test fails here, first check it is not one that dropped the conftest opt-out
(Task 1) — that is the one way a test can now reach a real apply.

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add src/cc_session_tools/cli/ccst.py src/cc_session_tools/lib/install_sync.py tests/test_ccst_main_gate.py tests/test_install_sync.py && git commit -m "feat(cli): auto-apply the install config instead of nudging and blocking

main() now calls ensure_synced() where the 2.4.0 gate used to sit. That
gate never fired for the case it was built for: it was guarded by
sys.stderr.isatty(), and Claude Code's Bash tool and every ccsched job
run without a TTY, so the nudge was invisible to exactly the automated
workflow that motivated it. A nudge was also the wrong remedy - what goes
stale is registration, and that is one idempotent command the tool can run
itself. should_block_for_unsynced_install is deleted, not deprecated: one
production caller, replaced here."
```

---

### Task 9: `ccst doctor` FAIL branch for an already-failed auto-apply

Spec's "`ccst doctor` change" section. `doctor` stays exempt from auto-applying, so it can always
tell the truth about this state.

- synced → `OK` (unchanged).
- out of sync, no failed attempt for this version → `WARN`, reason updated to say it will
  self-apply.
- out of sync **and** `failed_attempt.version == installed_version` → **`FAIL`**, naming the rc and
  timestamp. Not self-recoverable — auto-apply has already tried and will keep failing — which is
  exactly the WARN/FAIL criterion `check_install_everything_synced`'s existing docstring states.

**Files:**
- Modify: `src/cc_session_tools/lib/doctor.py:296-323` (the check) and `:758-773`/`:890`
  (`run_all_checks`)
- Modify: `src/cc_session_tools/cli/ccst.py:658-672` (`_cmd_doctor`'s call site)
- Test: `tests/test_ccst_doctor.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ccst_doctor.py`, next to the existing `check_install_synced` tests:

```python
def test_check_install_synced_warn_says_it_will_self_apply() -> None:
    result = doctor.check_install_everything_synced(
        installed_version="2.5.0", synced_version="2.4.0", failed_attempt=None
    )
    assert result.status == doctor.Status.WARN
    assert "automatically" in result.reason


def test_check_install_synced_fails_when_auto_apply_already_failed() -> None:
    """Not self-recoverable: auto-apply has already tried for this exact
    version and will keep failing until a human intervenes. That is the
    WARN/FAIL criterion this check's own docstring states."""
    from datetime import datetime, timezone

    from cc_session_tools.lib.install_sync import FailedAttempt

    result = doctor.check_install_everything_synced(
        installed_version="2.5.0",
        synced_version="2.4.0",
        failed_attempt=FailedAttempt(
            version="2.5.0", at=datetime(2026, 8, 15, 9, 12, 4, tzinfo=timezone.utc), rc=1
        ),
    )
    assert result.status == doctor.Status.FAIL
    assert "rc 1" in result.reason
    assert "2026-08-15T09:12:04Z" in result.reason


def test_check_install_synced_warns_when_the_failure_is_for_another_version() -> None:
    """A newer release may well fix the failing step, so a failure recorded
    against a different version must not FAIL the current one."""
    from datetime import datetime, timezone

    from cc_session_tools.lib.install_sync import FailedAttempt

    result = doctor.check_install_everything_synced(
        installed_version="2.5.0",
        synced_version="2.4.0",
        failed_attempt=FailedAttempt(
            version="2.4.0", at=datetime(2026, 8, 15, 9, 12, 4, tzinfo=timezone.utc), rc=1
        ),
    )
    assert result.status == doctor.Status.WARN


def test_check_install_synced_ok_ignores_a_stale_failure_record() -> None:
    from datetime import datetime, timezone

    from cc_session_tools.lib.install_sync import FailedAttempt

    result = doctor.check_install_everything_synced(
        installed_version="2.5.0",
        synced_version="2.5.0",
        failed_attempt=FailedAttempt(
            version="2.5.0", at=datetime(2026, 8, 15, 9, 12, 4, tzinfo=timezone.utc), rc=1
        ),
    )
    assert result.status == doctor.Status.OK


def test_run_all_checks_threads_the_failed_attempt_through(tmp_path: Path) -> None:
    """Wiring test: the unit tests above prove the branch, this proves doctor
    actually reaches it."""
    from datetime import datetime, timezone

    from cc_session_tools.lib.install_sync import FailedAttempt

    results = doctor.run_all_checks(
        installed_version="2.5.0",
        settings_path=tmp_path / "settings.json",
        bundle_path=tmp_path / "hooks-bundle.json",
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={},
        skip_pypi=True,
        synced_version="2.4.0",
        failed_attempt=FailedAttempt(
            version="2.5.0", at=datetime(2026, 8, 15, 9, 12, 4, tzinfo=timezone.utc), rc=1
        ),
    )
    synced = [r for r in results if r.name == "install:synced"]
    assert len(synced) == 1
    assert synced[0].status == doctor.Status.FAIL
```

(Match the surrounding file's existing import style for `doctor` and its `run_all_checks` call
conventions — check the neighbours before assuming the keyword set above is complete.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_doctor.py -k install_synced -v`
Expected: FAIL — `check_install_everything_synced() got an unexpected keyword argument
'failed_attempt'`.

- [ ] **Step 3: Implement**

In `src/cc_session_tools/lib/doctor.py`, add a TYPE_CHECKING import (the module already has
`from __future__ import annotations`, so the annotation costs nothing at runtime and there is no
import cycle to worry about — `install_sync` imports only `sessions_db`):

```python
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cc_session_tools.lib.install_sync import FailedAttempt
```

Replace `check_install_everything_synced` (`doctor.py:296-323`):

```python
def check_install_everything_synced(
    installed_version: str,
    synced_version: str | None,
    failed_attempt: FailedAttempt | None = None,
) -> CheckResult:
    """OK when the recorded sync marker matches the running ccst version.

    WARN when it doesn't: the next non-exempt `ccst` command auto-applies it,
    so this is self-recoverable with no user action at all - same severity
    rationale as check_ccsched_job_registered.

    FAIL when an auto-apply has already been attempted and failed for this
    exact version. That state is not self-recoverable: it will keep failing
    identically on every retry (a real ~/.claude/skills/<name> directory
    blocking a symlink, unbalanced CLAUDE.md sentinels) until a human looks at
    it. doctor is exempt from auto-applying, so it can always report this
    rather than silently erasing it.
    """
    name = "install:synced"
    if synced_version == installed_version:
        return CheckResult(
            name=name, status=Status.OK,
            reason=f"install-everything last synced at {installed_version}",
        )
    if failed_attempt is not None and failed_attempt.version == installed_version:
        return CheckResult(
            name=name, status=Status.FAIL,
            reason=(
                f"installed {installed_version}, and the automatic install sync already "
                f"failed for this version (rc {failed_attempt.rc} at "
                f"{failed_attempt.at.strftime('%Y-%m-%dT%H:%M:%SZ')}) — "
                "run `ccst install-everything --apply` to see why"
            ),
        )
    if synced_version is None:
        return CheckResult(
            name=name, status=Status.WARN,
            reason=(
                f"installed {installed_version}, install-everything has never been run — "
                "it will be applied automatically on the next `ccst` command; run "
                "`ccst install-everything --apply` to do it now"
            ),
        )
    return CheckResult(
        name=name, status=Status.WARN,
        reason=(
            f"installed {installed_version}, install-everything last synced at "
            f"{synced_version} — it will be applied automatically on the next `ccst` "
            "command; run `ccst install-everything --apply` to do it now"
        ),
    )
```

In `run_all_checks` (`doctor.py:758-773`), add the parameter next to `synced_version`:

```python
    failed_attempt: FailedAttempt | None = None,
```

document it in the docstring's Parameters block alongside `synced_version`, and update the call at
`doctor.py:890`:

```python
        check_install_everything_synced(installed_version, synced_version, failed_attempt)
```

In `src/cc_session_tools/cli/ccst.py`'s `_cmd_doctor` (`ccst.py:658-672`):

```python
        synced_version=install_sync.get_synced_version(),
        failed_attempt=install_sync.get_failed_attempt(),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_doctor.py -v`
Expected: PASS (whole file — existing `run_all_checks` callers that don't pass `failed_attempt`
still work via the default).

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add src/cc_session_tools/lib/doctor.py src/cc_session_tools/cli/ccst.py tests/test_ccst_doctor.py && git commit -m "feat(doctor): FAIL install:synced when auto-apply already failed

Out-of-sync is now a WARN that says nothing needs doing - the next ccst
command applies it. But an auto-apply that has already failed for this
exact version will keep failing identically until a human intervenes,
which is precisely the not-self-recoverable criterion this check's
docstring uses to separate WARN from FAIL. doctor stays exempt from
auto-applying so it can report this rather than erase it."
```

---

### Task 10: Docstrings and CLI help text

Explicit task rather than folded into the code commits, so nothing on the spec's list is left to
chance. Most items were rewritten in place by Tasks 2/3/4/5/7/8/9 — this task verifies each one and
fixes the two with no natural home.

**Files:**
- Modify: `src/cc_session_tools/lib/install_sync.py` (module docstring)
- Modify: `src/cc_session_tools/cli/ccst.py` (`install-everything` subparser `help=`, `:2176-2182`)
- Verify only: `doctor.py`, `scheduler/lock.py`, `proc_lock.py`, `main()`'s comment block

- [ ] **Step 1: Rewrite the `install_sync.py` module docstring**

It currently says the module "lets `main()` nudge an interactive user", which stopped being true
at Task 8. Replace the first paragraph:

```python
"""Tracks whether ~/.claude's registration matches the installed ccst version,
and brings it back into line when it doesn't.

`ccst install-everything --apply` (explicit, or the automatic one main() runs
through ensure_synced) records the version it last succeeded for; a failed
automatic attempt records the version, timestamp and rc so it backs off for
six hours instead of retrying on every invocation. `ccst doctor` reports both
facts as one check result.

Backed by the install_sync table in sessions.db, following the same
established pattern as doctor_mutes.py (see its module docstring and
sessions_db.py's): small persistent CLI state belongs in the shared
sessions.db file, not a bespoke JSON/db file per subsystem.
"""
```

- [ ] **Step 2: Update the `install-everything` subparser help text**

`ccst.py:2176-2182`:

```python
    ie_parser = sub.add_parser(
        "install-everything",
        help=(
            "Run all install steps (skills, hooks, shell, claude-md, scheduled jobs) then "
            "health-check. Dry run by default; pass --apply to write changes. ccst runs "
            "this for you automatically after an upgrade — use it directly to preview the "
            "changes, to pass per-category target overrides, or to see why an automatic "
            "sync failed."
        ),
    )
```

- [ ] **Step 3: Verify the rest of the spec's docstring list**

Confirm each of these already reads correctly after the earlier tasks; fix anything that still
describes the old behaviour:

- `install_sync.py` — `decide_auto_sync`, `ensure_synced`, `is_auto_sync_exempt`,
  `record_failed_attempt`, `get_failed_attempt`, `record_synced`, `FailedAttempt` all have
  docstrings (Tasks 3-5, 7).
- `ccst.py` — `main()`'s comment block rewritten for the new exempt set and the measured costs
  (Task 8); `run_install_everything` and `_cmd_install_everything` docstrings (Task 6).
- `doctor.py` — `check_install_everything_synced` docstring and both WARN reason strings, plus the
  FAIL branch (Task 9).
- `scheduler/lock.py` — module docstring now points at `proc_lock.py` for the mechanism (Task 2).
- `proc_lock.py` — module docstring names both callers (Task 2).

Grep for anything left behind:

```bash
grep -rn "nudge\|is_interactive\|should_block" src/ tests/
```

Expected: no hits in `src/`. Any hit is a stale comment describing behaviour that no longer exists.

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: PASS. Also eyeball `uv run python -m cc_session_tools.cli.ccst --help` and confirm the
`install-everything` line reads sensibly.

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add src/cc_session_tools/lib/install_sync.py src/cc_session_tools/cli/ccst.py && git commit -m "docs: update install_sync's module docstring and the CLI help text

The module docstring still described nudging an interactive user, which
stopped being what this code does. The install-everything help text now
says ccst runs it automatically and names the three reasons to run it by
hand: previewing, per-category target overrides, and diagnosing a failed
automatic sync."
```

---

### Task 11: `README.md`

Five existing spots plus one new subsection. Line references are from the current file and will
drift as you edit — work top to bottom.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite the "After upgrading" callout (L90-95)**

Replace the existing blockquote with:

```markdown
> **After upgrading** (`uv tool install --reinstall`/`--upgrade`, or `pip`/`pipx` equivalents):
> nothing to do. The next `ccst` command notices that the installed version doesn't match the
> version your config was last synced to, runs `install-everything --apply` itself, and tells you
> on stderr that it did. See [Automatic install sync](#automatic-install-sync) below for the exempt
> commands, the `CCST_NO_AUTO_SYNC=1` escape hatch, and what happens when the sync fails.
```

- [ ] **Step 2: Collapse "Manual path" step 2 (L103-107)**

Replace the three individual `install --apply` lines with:

```sh
# 2. Install bundled skills, hooks, the ccl shell function, the CLAUDE.md
#    messaging block and the scheduled jobs (idempotent — safe to re-run)
ccst install-everything --apply
```

- [ ] **Step 3: Collapse the "Upgrade" step 2 (L143-150)**

```sh
# 2. Optional — the next ccst command does this for you automatically
ccst install-everything --apply

# 3. Verify
ccst doctor
```

- [ ] **Step 4: Add the new "Automatic install sync" subsection**

Under `## Installation`, after the upgrade block. (The block below is fenced with four backticks
because its own content contains a three-backtick fence — write three into the README.)

````markdown
### Automatic install sync

`uv`, `pip` and `pipx` run no code after unpacking a wheel, so nothing at install time can wire a
newly-added skill or hook into `~/.claude`. Code is fine — skills symlinks and `ccst hooks run`
resolve into the tools path the installer reuses in place — but *registration* isn't: a hook added
in a newer version isn't in `settings.json` until `install-everything` runs, so it silently never
fires.

`ccst` closes that gap itself. Before dispatching any command, it compares the installed version
against the version your config was last synced to. On a mismatch it runs the five install steps
(skills, hooks, shell, CLAUDE.md, scheduled jobs), prints two lines to **stderr**, and then runs
the command you actually asked for:

```
ccst: install config is out of sync (installed 2.5.0, last synced 2.4.0) — syncing…
ccst: install config synced to 2.5.0.
```

- **Nothing is written to stdout**, so `--json` output and scripted callers are unaffected, and
  **your command's exit code is never changed** by the sync.
- **Exempt commands** (never trigger a sync): `ccst hooks run <verb>` (fires on every tool call in
  every open Claude Code session), `install-everything`, `doctor`, `repair`, `migrate`, and any
  `install`/`uninstall` verb — those are you driving install state by hand, possibly with your own
  `--target`, and a default-target sync underneath them would be self-contradictory.
- **`CCST_NO_AUTO_SYNC=1`** disables it entirely, for CI, bisecting, or any environment where
  `~/.claude` must not be touched.
- **If a sync fails** (for example `~/.claude/skills/<name>` exists as a real directory rather than
  a symlink), the full step output is printed to stderr, the failure is remembered, and it backs
  off for six hours rather than retrying on every command — printing one warning line each time
  instead. `ccst doctor` reports that state as a **FAIL**, since it will not recover on its own.
  Run `ccst install-everything --apply` to see the whole thing and fix it.
- **If you never run `ccst` by hand**, the bundled daily `pdata-verify-all` scheduled job does, so
  a machine syncs itself within about 24 hours with no user action.
````

- [ ] **Step 5: Add the advanced-use preamble to the four partial install sections**

Each of `### ccst hooks install` (L692), `### ccst skills install` (L744), `### ccst shell install`
(L775) and `### ccst claude-md install` (L812) gets a one-line preamble immediately under the
heading. Do **not** delete these sections — they are the implementation, they carry per-category
overrides `install-everything` cannot express, and single-category dry-run is the debugging tool of
choice when one step of a five-step apply fails (§8):

```markdown
> You normally don't need this: `ccst install-everything --apply` runs it, and `ccst` runs that
> for you automatically after an upgrade. Use it directly for a custom `--target`/`--source`/
> `--hook`, or to dry-run one category on its own.
```

Adjust the trailing option list per section (`hooks install` has `--source`/`--hook`/`--target`,
`skills install` has `--source`/`--target`/`--force`, `shell install` has `--fragments-dir`,
`claude-md install` has `--target`).

- [ ] **Step 6: Update the `ccst doctor` section (L832-840)**

Replace the `install-everything` clause in the prose list:

```markdown
whether your config is in sync with the installed version (WARN if not — the next `ccst` command
fixes it; FAIL if an automatic sync already tried and failed, which needs you),
```

- [ ] **Step 7: Fix the two claims Task 12 makes false**

Neither is on the spec's list, but both stop being true once `install-everything.sh` changes:

- **L88's note** ("`install-everything.sh` handles steps 1–5 of setup. Step 6 — adding broader CCST
  guidance to your global `~/.claude/CLAUDE.md` … — is interactive"). The script's own step labels
  drop from six to three in Task 12. Reword to reference the remaining interactive step by name
  rather than by number:

  ```markdown
  > **Note:** `install-everything.sh` does everything except one interactive step — adding broader
  > CCST guidance to your global `~/.claude/CLAUDE.md` (session management, 8-digit gate, etc.),
  > which must be run separately afterwards. See [Configure your global
  > CLAUDE.md](#configure-your-global-claudemd) below.
  ```

- **L460** ("The `install-everything.sh` script runs `ccst claude-md install --apply`
  automatically as part of the standard installation sequence"). It now reaches that step through
  `ccst install-everything --apply`. Reword to "…runs this step for you, via `ccst
  install-everything --apply`, as part of the standard installation sequence."

- [ ] **Step 8: Verify**

```bash
grep -n "prints a nudge\|exits until you do\|steps 1–5 of setup" README.md
```

Expected: no hits.

- [ ] **Step 9: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add README.md && git commit -m "docs(readme): document auto-apply, demote the partial install commands

The recommended flows now use install-everything --apply rather than
listing skills/hooks/shell individually, and the upgrade step is marked
optional because the next ccst command does it. Adds an 'Automatic install
sync' section covering the trigger, the two stderr lines, the exempt set,
CCST_NO_AUTO_SYNC, the failure/backoff behaviour and the daily ccsched
backstop. The five partial install commands stay documented - they carry
per-category overrides install-everything can't express - but each now
opens by saying you don't normally need it."
```

---

### Task 12: `install-everything.sh`

§8. The script currently shells out to four of the five partial commands individually.

**Spec gap 4:** the spec's note covers the closing "For future upgrades" line at L95 but not the
identical claim in the usage header at L20-22. **Call: update both** — they say the same thing and
leaving one stale is worse than leaving neither.

**Files:**
- Modify: `install-everything.sh`

- [ ] **Step 1: Replace steps 2-5 with one step**

Replace lines 69-84 (the four `step`/`ccst … install --apply` pairs) with:

```bash
# ── Step 2: Skills, hooks, shell helpers, CLAUDE.md, scheduled jobs ──────────
step "2/3  Install config"
ccst install-everything --apply
```

- [ ] **Step 2: Renumber the remaining labels**

`step "1/6  CLIs"` → `step "1/3  CLIs"` (L46), and `step "6/6  Health check"` →
`step "3/3  Health check"` (L86). Note `ccst install-everything --apply` already ends with its own
health check, so step 3's `ccst doctor` is now a second run — keep it: it is the script's visible
"here is your machine's state" output, and it is what the closing message refers to.

- [ ] **Step 3: Update both "future upgrades" claims**

L20-22 (usage header):

```bash
# For FUTURE UPGRADES (once the CLIs are already installed), you only need:
#   uv tool install cc-session-tools --upgrade
# ccst syncs its own config on the next command you run.
```

L94-95 (closing message):

```bash
echo "For future upgrades, you only need:"
echo "  uv tool install cc-session-tools --upgrade"
echo "  (ccst syncs its own config on the next command you run.)"
```

- [ ] **Step 4: Verify**

```bash
bash -n install-everything.sh
grep -n "ccst skills install\|ccst hooks install\|ccst shell install\|ccst claude-md install" install-everything.sh
```

Expected: `bash -n` silent (syntax OK), grep returns nothing.

Do **not** actually run the script — it installs against the real `~/.claude`.

- [ ] **Step 5: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add install-everything.sh && git commit -m "build: use install-everything --apply in the bootstrap script

Four separate partial-install invocations replaced by the one command
that already dispatches to all five steps, including the scheduled-jobs
step the script was silently missing. The 'for future upgrades' guidance
in both the usage header and the closing message now stops at the package
upgrade, since ccst syncs its own config on the next command."
```

---

### Task 13: `.claude/CLAUDE.md`

**Files:**
- Modify: `.claude/CLAUDE.md` ("After merging a PR" section)

- [ ] **Step 1: Add the note**

After the existing `uv tool install --reinstall ~/repos/claude-code-session-tools` block and its
`--reinstall` explanation, add:

```markdown
No follow-up `ccst install-everything --apply` is needed: the reinstall changes the installed
version, and the next `ccst` command notices the mismatch and syncs skills, hooks, shell
functions, `CLAUDE.md` and scheduled jobs itself. Run it by hand only to see the full five-step
output, or when `ccst doctor` FAILs `install:synced` (which means an automatic sync already tried
and failed).
```

- [ ] **Step 2: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add .claude/CLAUDE.md && git commit -m "docs: note that a reinstall no longer needs a manual install-everything

The post-merge reinstall step used to leave the machine's config a
version behind until someone remembered to re-run install-everything.
ccst now does that on its next invocation."
```

---

### Task 14: Full-suite verification and manual smoke test

This changes every `ccst` invocation's entrypoint and writes to `~/.claude` without being asked.
Verify it on a real machine, in a sandbox, before shipping.

**Files:** none (verification only)

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: all pass, 0 failures. "Tests pass" means every configured check exits 0 — run the repo's
type-check and lint steps too if `pyproject.toml`/CI defines them.

- [ ] **Step 2: Confirm no test can reach the real `~/.claude`**

```bash
ls -la --time-style=full-iso ~/.claude/settings.json ~/.claude/skills | head
uv run pytest -q
ls -la --time-style=full-iso ~/.claude/settings.json ~/.claude/skills | head
```

Expected: identical mtimes before and after. This is Task 1's whole purpose; verify it rather than
assume it.

- [ ] **Step 3: Sandboxed end-to-end smoke test**

```bash
export SMOKE=$(mktemp -d)
mkdir -p "$SMOKE/home/.claude"
echo '{}' > "$SMOKE/home/.claude/settings.json"

# NOTE: `env`'s -u options MUST come before the NAME=VALUE assignments — GNU env
# stops parsing options at the first non-option argument, so `env HOME=x -u FOO cmd`
# fails with "env: '-u': No such file or directory".
run_ccst() {
  env -u CCST_NO_AUTO_SYNC \
      HOME="$SMOKE/home" \
      CCST_DATA_HOME="$SMOKE/data" \
      CCST_SESSIONS_DIR="$SMOKE/sessions" \
      CC_SCHEDULER_DIR="$SMOKE/scheduler" \
      uv run python -m cc_session_tools.cli.ccst "$@"
}

# 1. First carrier invocation on a never-synced sandbox: expect the two stderr
#    lines, and the command's own output on stdout.
run_ccst sessions list --json

# 2. Second invocation: expect NO sync lines (marker now matches).
run_ccst sessions list --json

# 3. Exempt: expect no sync lines even though this is a fresh sandbox.
rm -rf "$SMOKE/sessions" "$SMOKE/data"
run_ccst doctor --no-pypi | tail -20

# 4. Stdout discipline: nothing but JSON on stdout.
rm -rf "$SMOKE/sessions" "$SMOKE/data"
run_ccst sessions list --json 2>/dev/null | python3 -m json.tool > /dev/null && echo "stdout is clean JSON"

# 5. Exit-code independence: pick any carrier command you can make fail for its
#    own reasons in this sandbox (check `ccst <noun> --help` for the exact flags
#    rather than copying a guess), run it on a wiped sandbox so a sync fires at
#    the same time, and confirm the rc is the command's own — not 0, not the
#    sync's.
rm -rf "$SMOKE/sessions" "$SMOKE/data"
run_ccst <a-command-that-fails> ; echo "rc=$?"
```

Expected: (1) two stderr lines then the JSON; (2) JSON only; (3) doctor's report with a WARN
`install:synced` line and no sync lines; (4) `stdout is clean JSON`; (5) the command's own rc.
Step 5 is a sanity check on top of Task 8's parametrized `test_exit_code_is_the_commands_own_
regardless_of_auto_sync`, which covers rc 0/1/3 deterministically — that test is the real
guarantee.

- [ ] **Step 4: Sandboxed failure-path smoke test**

```bash
rm -rf "$SMOKE/sessions" "$SMOKE/data"
mkdir -p "$SMOKE/home/.claude/skills/notify-user"   # a real dir where a symlink belongs
echo x > "$SMOKE/home/.claude/skills/notify-user/SKILL.md"

run_ccst sessions list --json          # expect: full step output on stderr + failure pointer
run_ccst sessions list --json          # expect: ONE backoff warning line, no step output
run_ccst doctor --no-pypi | grep install:synced   # expect: FAIL, naming the rc and timestamp
```

- [ ] **Step 5: Clean up**

```bash
rm -rf "$SMOKE"
```

- [ ] **Step 6: Commit (only if anything needed fixing)**

No commit if all six steps passed as written.

---

### Task 15: Version bump, CHANGELOG, release

§11: **2.5.0, minor.** Not major — no on-disk data is relocated or reformatted (the three failure
keys are rows in the existing `install_sync` KV table, so no DDL change, no migration, and no
`ccst doctor` FAIL-until-migrated check is owed), and no documented CLI subcommand or flag is
removed (§8 keeps all five partial install commands).
`should_block_for_unsynced_install` is an internal library function with one in-repo caller, not
public surface. Not patch — it is a deliberate behaviour change plus a new env var.

**Files:**
- Modify: `pyproject.toml:7`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump the version**

`pyproject.toml:7`: `version = "2.4.0"` → `version = "2.5.0"`.

- [ ] **Step 2: Add the CHANGELOG entry**

Insert under `## [Unreleased]`, above `## [2.4.0]`. Do not edit 2.4.0's history.

```markdown
## [2.5.0] - 2026-08-15

### Changed

- **`ccst` now syncs its own install config automatically instead of nudging you to.** After an
  upgrade, the next non-exempt `ccst` command runs `install-everything --apply` itself, prints two
  lines to stderr saying it did, and then runs the command you asked for. 2.4.0's nudge-and-block
  gate is gone: it was guarded by `sys.stderr.isatty()`, and neither Claude Code's Bash tool nor a
  scheduled `ccsched` job has a TTY, so it never fired for the automated workflows that motivated
  it — and a nudge was the wrong remedy anyway, since what goes stale on an upgrade is
  *registration* (a new hook isn't in `settings.json` until `install-everything` runs) and that is
  one idempotent command the tool can run itself. `is_interactive` no longer gates anything
  anywhere.
- The auto-sync never writes to stdout and never changes the invocation's exit code. `--json`
  output stays machine-readable, and `ccsched`'s failure ledger can't mistake an install problem
  for a job failure.
- **`ccst doctor`'s `install:synced` WARN** now says the config will be applied automatically on
  the next `ccst` command, rather than telling you to run it.
- `install-everything.sh` runs `ccst install-everything --apply` instead of four separate partial
  install commands (which also means it now registers the bundled scheduled jobs, which it was
  silently skipping). README's recommended flows do the same; the five partial `install`
  subcommands are unchanged and still documented, now marked as advanced use for custom
  `--target`/`--source`/`--hook` and single-category dry runs.

### Added

- **`CCST_NO_AUTO_SYNC=1`** disables the automatic sync entirely — for CI, for bisecting, and for
  any environment where `~/.claude` must not be touched.
- **Failure backoff.** A sync that fails does so persistently (a real `~/.claude/skills/<name>`
  directory where a symlink belongs, unbalanced `CLAUDE.md` sentinels), so the version, timestamp
  and rc are recorded and it backs off for six hours rather than reprinting a failing five-step
  install on every command. Installing a different version resets it. The record is cleared by any
  successful sync, including an explicit `ccst install-everything --apply`.
- **`ccst doctor` FAILs `install:synced`** when an automatic sync has already tried and failed for
  the installed version, naming the rc and timestamp. That state is not self-recoverable, which is
  this check's own WARN/FAIL criterion.
- An exclusive, try-once apply lock (`~/.local/share/claude/.install-sync.lock`) so a scheduled job
  and an interactive command can't apply concurrently. A contender skips rather than waiting — the
  winner is applying the same thing.

### Exempt from the automatic sync

`ccst hooks run <verb>` (fires on every tool call in every open session, and must not rewrite
`settings.json` mid-session from inside a hook Claude Code invoked from `settings.json`),
`install-everything`, `doctor`, `repair`, `migrate`, any `install`/`uninstall` verb, and anything
run with `CCST_NO_AUTO_SYNC=1`. Machines where nobody ever types a `ccst` command still self-heal
within ~24h via the bundled daily `pdata-verify-all` scheduled job.
```

- [ ] **Step 3: Verify**

Run: `uv run pytest -q && uv run python -m cc_session_tools.cli.ccst --version`
Expected: PASS, and `2.5.0`.

- [ ] **Step 4: Commit**

```bash
cd ~/repos/claude-code-session-tools/.worktrees/install-sync-auto-apply && git add pyproject.toml CHANGELOG.md && git commit -m "chore(release): bump to 2.5.0 for install-sync auto-apply

Minor per this repo's version policy: no on-disk data is relocated or
reformatted (the three failure keys are rows in the existing install_sync
KV table, so no DDL change and no migration guard is owed) and no
documented CLI subcommand or flag is removed. Not a patch either - it is
a deliberate behaviour change plus a new env var."
```

- [ ] **Step 5: Ship**

Push, open the PR, wait for CI, merge. The `ship-feature-branch` skill covers this end to end.

Then, **from outside the worktree**, refresh the global install:

```bash
cd ~/repos/claude-code-session-tools && git checkout main && git pull
uv tool install --reinstall ~/repos/claude-code-session-tools
```

`--reinstall` is required: without it, uv sees an unchanged version number and silently skips the
build. No follow-up `ccst install-everything --apply` — that is the whole point of this change;
confirm it by running any `ccst` command and watching for the two stderr lines.

- [ ] **Step 6: Tag and release**

```bash
cd ~/repos/claude-code-session-tools && git tag v2.5.0 && git push --tags
```

Then create a GitHub Release from the tag — `.github/workflows/release.yml` fires automatically,
builds the wheel and sdist, attaches them to the release, and publishes to PyPI via the configured
Trusted Publisher.

---

## Out of scope (deliberately, per the spec's non-goals)

- **A post-install wheel hook.** `pip`/`uv` run no arbitrary code after unpacking. This is a hard
  constraint, not an unexplored option.
- **Auto-sync from the other seven console scripts** (`ccd`, `ccr`, `ccs`, `ccmsg`, `ccsched`,
  `claude-code-usage`, `cccs-stats`). One entry point means one decision site, one place to test,
  and one place a mistake can break — and a mistake in `ccd`/`ccr` breaks session launch itself.
  The daily-`ccsched` backstop already covers the "human only ever runs `ccl`" case within a day.
  Revisit only if that latency proves too slow in practice.
- **Auto-upgrading the package itself.** `ccst doctor`'s PyPI drift check stays a WARN; installing
  a new version remains a deliberate human act. This change only syncs config to whatever version
  is already installed.
- **Any change to `claude-code-config-sync` (CCCS).** CCST writing hook registrations into
  `~/.claude/settings.json` already creates local-ahead drift against CCCS today; what is new is
  that it now happens without a human having typed the command. The interaction is bounded and
  already safe — `prune_stale_hooks` only touches entries whose command starts with
  `ccst hooks run `, leaving every foreign entry (including CCCS's own) untouched. If the drift
  warning becomes noisy, the correct follow-up is a CCCS-side change.
- **Removing any of the five partial install subcommands.** §8: they *are* the implementation,
  they carry overrides `install-everything` cannot express per category, single-category dry-run is
  the debugging tool for a failed step, `_cmd_hooks_run`'s own stale-hook error message points at
  them, and removing a documented subcommand would be a breaking change requiring 3.0.0.
- **A SessionStart self-heal hook.** Structurally impossible to bootstrap: a new hook verb is not
  in `settings.json` until `install-everything` has run, which is the very thing being fixed.
