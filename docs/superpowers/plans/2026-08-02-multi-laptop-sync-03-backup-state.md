# Plan 3: `ccst backup-state` + bundled job + staleness nudge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Read
> `docs/superpowers/plans/2026-08-02-multi-laptop-sync-00-overview.md` first for binding
> cross-plan decisions. **Task 4 of this plan depends on Plan 1** (it extends
> `_cmd_pdata_sync_push`/`_cmd_pdata_sync_pull_project`/`_cmd_pdata_sync_pull_all` in
> `cli/ccst.py`, all built by
> `docs/superpowers/plans/2026-08-02-multi-laptop-sync-01-core-sync.md`) — land Plan 1 first, or
> at minimum before starting this plan's Task 4. Tasks 1-3 and 5 have no dependency on either
> other plan.

**Goal:** a local-only `ccst backup-state` command that snapshots CCST's own machine-state
databases (`sessions.db`, `ccmsg.db`, `ccsched.db`, `telemetry.db`, `command-cache.db`) for
disaster recovery — never synced across laptops, unlike `ccst pdata sync` (Plan 1). A bundled
`ccsched` job tracks when it last ran (for the nudge below) without ever auto-firing. `ccst pdata
sync push`/`pull` print a gentle nudge if this hasn't run in a while.

**Architecture:** `lib/backup_state.py` reuses Plan 1's `running_gate` + the existing
`db.backup_to()` primitive for the actual copy, and mirrors `ccsched run <id>`'s existing pattern
(`cli/ccsched.py::_cmd_run`) for updating `job_state` directly, bypassing ccsched's own runner
entirely — this job must never be auto-triggered by a cadence. `BundledJob` gains an `enabled`
field so `ccst ccsched-jobs install` can register it non-firing.

**Tech Stack:** Python 3.11+, stdlib `argparse`/`sqlite3`/`datetime`, `pytest`.

---

## File Structure

- Create: `src/cc_session_tools/lib/backup_state.py`
- Create: `tests/test_backup_state.py`
- Modify: `src/cc_session_tools/lib/scheduler/bundled_jobs.py` — add `enabled` field + the
  `backup-state` entry
- Modify: `tests/test_scheduler_bundled_jobs.py`
- Modify: `src/cc_session_tools/cli/ccst.py:1351-1356` — thread `enabled=job.enabled` through
  `_cmd_ccsched_jobs_install`
- Modify: `tests/test_ccst_ccsched_jobs_cli.py`
- Modify: `src/cc_session_tools/cli/ccst.py` — new `backup-state` noun (parser near `doctor`,
  `ccst.py:1587`; handler near `_cmd_doctor`; dispatch near `ccst.py:2085`)
- Create: `tests/test_ccst_backup_state_cli.py`
- Modify (Plan-1-dependent): `src/cc_session_tools/cli/ccst.py`'s `_cmd_pdata_sync_push` /
  `_cmd_pdata_sync_pull_project` / `_cmd_pdata_sync_pull_all`, and
  `tests/test_ccst_pdata_sync_cli.py`
- Modify: `pyproject.toml`, `CHANGELOG.md` (last task — version bump)

---

### Task 1: `lib/backup_state.py`

**Files:**
- Create: `src/cc_session_tools/lib/backup_state.py`
- Test: `tests/test_backup_state.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backup_state.py
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib import backup_state


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def _gate_always_ok(monkeypatch):
    from cc_session_tools.lib import running_gate
    monkeypatch.setattr(
        running_gate, "check_safe_to_copy", lambda db_path: running_gate.GateResult(ok=True)
    )


def _write_fake_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()


def test_run_backup_copies_existing_stores_and_skips_missing(tmp_path):
    _write_fake_db(tmp_path / "sessions.db")
    result = backup_state.run_backup()
    assert result.backed_up == ("sessions.db",)
    assert "ccmsg.db" in result.skipped_missing
    backups = list(backup_state.backup_dir().glob("*/sessions.db"))
    assert len(backups) == 1


def test_run_backup_copies_every_store_that_exists(tmp_path):
    _write_fake_db(tmp_path / "sessions.db")
    _write_fake_db(tmp_path / "ccmsg.db")
    result = backup_state.run_backup()
    assert set(result.backed_up) == {"sessions.db", "ccmsg.db"}


def test_run_backup_raises_when_gate_blocks(tmp_path, monkeypatch):
    from cc_session_tools.lib import running_gate
    _write_fake_db(tmp_path / "sessions.db")
    monkeypatch.setattr(
        running_gate, "check_safe_to_copy",
        lambda db_path: running_gate.GateResult(ok=False, reasons=("claude is running",)),
    )
    with pytest.raises(RuntimeError, match="claude is running"):
        backup_state.run_backup()


def test_staleness_nudge_never_run():
    msg = backup_state.staleness_nudge()
    assert msg is not None
    assert "never run" in msg


def test_staleness_nudge_none_when_fresh():
    backup_state.record_job_success()
    assert backup_state.staleness_nudge() is None


def test_staleness_nudge_stale_after_threshold():
    from cc_session_tools.lib.scheduler import state
    old = datetime.now(timezone.utc) - timedelta(days=10)
    old_ts = state.format_ts(old)
    state.ensure_registered_db(backup_state.JOB_ID, old)
    state.record_success(backup_state.JOB_ID, new_success=old_ts, attempt_ts=old_ts)
    msg = backup_state.staleness_nudge(threshold_days=7)
    assert msg is not None
    assert "10 day" in msg


def test_staleness_nudge_fresh_within_threshold():
    from cc_session_tools.lib.scheduler import state
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    recent_ts = state.format_ts(recent)
    state.ensure_registered_db(backup_state.JOB_ID, recent)
    state.record_success(backup_state.JOB_ID, new_success=recent_ts, attempt_ts=recent_ts)
    assert backup_state.staleness_nudge(threshold_days=7) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backup_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.backup_state'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/backup_state.py
"""Machine-local backup for CCST's own infrastructure databases (spec
§4.1, §5's `ccst backup-state`) — distinct from `ccst pdata sync`
(lib/pdata/sync.py), which moves project data between laptops. This backs
up sessions.db, ccmsg.db, ccsched.db, telemetry.db, and command-cache.db to
a local, timestamped directory; nothing here ever leaves this machine."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import db, paths, running_gate

JOB_ID = "backup-state"
BACKUP_DIRNAME = "state-backups"

# The five machine-local .db files spec §4.1 says stay local — never synced,
# unlike project-db/<project>.db (handled by lib/pdata/sync.py).
MACHINE_STATE_STORES = (
    "sessions.db", "ccmsg.db", "ccsched.db", "telemetry.db", "command-cache.db",
)


def _timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def backup_dir() -> Path:
    return paths.data_home() / BACKUP_DIRNAME


@dataclass(frozen=True, slots=True)
class BackupResult:
    backed_up: tuple[str, ...]
    skipped_missing: tuple[str, ...]


def run_backup() -> BackupResult:
    """Copy every existing machine-state .db into a new timestamped
    directory under backup_dir(), gated by the same running-session check as
    pdata sync (spec §4.4 — "applies to both"). A store that doesn't exist
    yet (never touched on this machine) is skipped, not an error."""
    dest_dir = backup_dir() / _timestamp()
    backed_up: list[str] = []
    skipped_missing: list[str] = []
    for name in MACHINE_STATE_STORES:
        source = paths.data_home() / name
        if not source.is_file():
            skipped_missing.append(name)
            continue
        gate = running_gate.check_safe_to_copy(source)
        if not gate.ok:
            raise RuntimeError(
                f"refusing to back up {name} while unsafe:\n"
                + "\n".join(f"  - {r}" for r in gate.reasons)
            )
        db.backup_to(source, dest_dir / name)
        backed_up.append(name)
    return BackupResult(backed_up=tuple(backed_up), skipped_missing=tuple(skipped_missing))


def record_job_success() -> None:
    """Stamp job_state's last_success for JOB_ID directly — mirrors
    `ccsched run <id>`'s own pattern (cli/ccsched.py::_cmd_run) exactly,
    bypassing ccsched's runner entirely, since this job is registered
    enabled=False and must never auto-fire (see bundled_jobs.py)."""
    from cc_session_tools.lib.scheduler import state

    now = datetime.now(timezone.utc)
    attempt_ts = state.format_ts(now)
    state.ensure_registered_db(JOB_ID, now)
    state.record_success(JOB_ID, new_success=attempt_ts, attempt_ts=attempt_ts)


def staleness_nudge(*, threshold_days: int = 7) -> str | None:
    """A one-line nudge for `ccst pdata sync push/pull` to print if
    backup-state hasn't run in a while, or has never run — None if it's
    fresh enough to say nothing (spec §5.1). An unreadable ccsched.db
    degrades to "never run" rather than blocking the caller's actual sync
    operation over a decorative nudge."""
    from cc_session_tools.lib.scheduler import state

    try:
        job_state = state.get_state(JOB_ID)
    except sqlite3.Error:
        return None
    if job_state is None or job_state.last_success is None:
        return "machine-state backup has never run — consider running 'ccst backup-state'"
    last = state.parse_ts_or_none(job_state.last_success)
    if last is None:
        return None
    age_days = (datetime.now(timezone.utc) - last).days
    if age_days < threshold_days:
        return None
    return (
        f"machine-state backup last ran {age_days} day(s) ago — "
        "consider running 'ccst backup-state'"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backup_state.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/backup_state.py tests/test_backup_state.py
git commit -m "feat: add machine-local backup for CCST's own infra databases"
```

---

### Task 2: Register `backup-state` as a disabled bundled `ccsched` job

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/bundled_jobs.py`
- Modify: `src/cc_session_tools/cli/ccst.py:1351-1356`
- Modify: `src/cc_session_tools/lib/doctor.py:189-212, 695-696` — see the doctor-check fix below,
  required in this same task
- Modify: `tests/test_scheduler_bundled_jobs.py`
- Modify: `tests/test_ccst_ccsched_jobs_cli.py`
- Modify: `tests/test_ccst_doctor.py`

**Doctor-check fix required in this task, not a follow-up:** `lib/doctor.py`'s
`check_ccsched_job_registered` (called once per bundled job in a loop at `doctor.py:695-696`)
currently WARNs whenever a bundled job's registered `enabled` is `False`, with the remediation
"run 'ccsched enable {job_id}'". Once this task registers `backup-state` with `enabled=False` by
design, that check would WARN on every single `ccst doctor` run forever, with actively wrong
advice — enabling it would make it auto-fire on a cadence, defeating the entire point. Fix
`check_ccsched_job_registered` to accept the *expected* enabled state and only WARN on a mismatch,
in whichever direction is actually wrong.

Confirmed via investigation: `enabled=False` is the exact, existing mechanism for "registered,
but never auto-fires" — `reconcile_and_launch` (`lib/scheduler/reconcile.py:50-51`) skips a job
the instant `not spec.enabled`, *before* it ever parses or evaluates `cadence`. `cadence` must
still be syntactically valid (there is no `"manual"` cadence value), but it becomes inert once
`enabled=False`. Nothing filters `enabled` out of `ccsched list`/`show`/doctor output — only the
auto-run path checks it — so `backup-state` still shows up everywhere a human or `ccst doctor`
looks for it.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_scheduler_bundled_jobs.py

def test_bundled_jobs_contains_backup_state_job():
    ids = [job.job_id for job in bundled_jobs.BUNDLED_CCSCHED_JOBS]
    assert "backup-state" in ids


def test_backup_state_job_is_registered_disabled():
    job = next(j for j in bundled_jobs.BUNDLED_CCSCHED_JOBS if j.job_id == "backup-state")
    assert job.enabled is False
    assert job.command == ("ccst", "backup-state")


def test_other_bundled_jobs_default_to_enabled():
    for job in bundled_jobs.BUNDLED_CCSCHED_JOBS:
        if job.job_id == "backup-state":
            continue
        assert job.enabled is True
```

```python
# append to tests/test_ccst_ccsched_jobs_cli.py

def test_apply_registers_backup_state_as_disabled(base_env):
    _run(base_env, "ccsched-jobs", "install", "--apply")
    r = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", "show", "backup-state"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=base_env,
    )
    assert r.returncode == 0, r.stderr
    assert re.search(r"enabled:\s+false", r.stdout)
```

```python
# append to tests/test_ccst_doctor.py

def test_check_ccsched_job_registered_ok_when_intentionally_disabled(monkeypatch, tmp_path) -> None:
    """backup-state (and any future bundled job registered enabled=False on purpose) must not
    WARN forever just for being in its intended state."""
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    from cc_session_tools.lib.scheduler import registry
    from cc_session_tools.lib.scheduler.jobspec import validate_job_fields

    spec = validate_job_fields(
        job_id="backup-state", cadence="daily@03:00", coalesce="one",
        command=["ccst", "backup-state"],
        surface=False, enabled=False, catchup_window="7d", timeout="120s",
    )
    registry.add_job(spec)

    result = check_ccsched_job_registered("backup-state", expected_enabled=False)
    assert result.status == Status.OK


def test_check_ccsched_job_registered_warns_when_should_be_disabled_but_isnt(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    from cc_session_tools.lib.scheduler import registry
    from cc_session_tools.lib.scheduler.jobspec import validate_job_fields

    spec = validate_job_fields(
        job_id="backup-state", cadence="daily@03:00", coalesce="one",
        command=["ccst", "backup-state"],
        surface=False, enabled=True, catchup_window="7d", timeout="120s",
    )
    registry.add_job(spec)

    result = check_ccsched_job_registered("backup-state", expected_enabled=False)
    assert result.status == Status.WARN
    assert "disable" in result.reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scheduler_bundled_jobs.py tests/test_ccst_ccsched_jobs_cli.py tests/test_ccst_doctor.py -v`
Expected: FAIL — `StopIteration` (no `backup-state` job in `BUNDLED_CCSCHED_JOBS` yet); the two new
doctor tests fail with `TypeError: check_ccsched_job_registered() got an unexpected keyword
argument 'expected_enabled'`

- [ ] **Step 3: Write the implementation**

Modify the `BundledJob` dataclass (`bundled_jobs.py:10-19`) to add `enabled`:

```python
@dataclass(frozen=True, slots=True)
class BundledJob:
    job_id: str
    cadence: str
    coalesce: str
    catchup_window: str
    timeout: str
    surface: bool
    command: tuple[str, ...]
    success_exit_codes: tuple[int, ...] = (0,)
    enabled: bool = True
```

Add the new entry to `BUNDLED_CCSCHED_JOBS` (`bundled_jobs.py:22-49`), after the `pdata-verify-all`
entry:

```python
    BundledJob(
        job_id="backup-state",
        # Inert: enabled=False means reconcile_and_launch (scheduler/reconcile.py:50-51)
        # skips this job before cadence is ever parsed — it never auto-fires. Registered
        # purely so `ccst backup-state`'s own record_job_success() has a job_state row to
        # update (spec §5.1's "free last-run tracking via job_state" plan).
        cadence="daily@03:00",
        coalesce="one",
        catchup_window="7d",
        timeout="120s",
        surface=False,
        command=("ccst", "backup-state"),
        enabled=False,
    ),
```

Thread `enabled=job.enabled` through `_cmd_ccsched_jobs_install`'s `validate_job_fields(...)` call
(`ccst.py:1351-1356`), replacing the hardcoded `True`:

```python
        spec = validate_job_fields(
            job_id=job.job_id, cadence=job.cadence, coalesce=job.coalesce,
            command=list(job.command), surface=job.surface, enabled=job.enabled,
            catchup_window=job.catchup_window, timeout=job.timeout,
            success_exit_codes=job.success_exit_codes,
        )
```

Fix `check_ccsched_job_registered` (`doctor.py:189-212`) to take the expected enabled state and
only WARN on a mismatch, in whichever direction is wrong:

```python
def check_ccsched_job_registered(job_id: str, *, expected_enabled: bool = True) -> CheckResult:
    """WARN (not FAIL) if a CCST-bundled ccsched job (lib/scheduler/bundled_jobs.py) is missing or
    not in its intended enabled state — recoverable by re-running `ccst ccsched-jobs install
    --apply` / `ccsched enable|disable <id>`, not a silent-data-loss risk (this repo's version
    policy reserves FAIL for breaking on-disk migrations, which this is not).

    expected_enabled mirrors the BundledJob's own `enabled` field (bundled_jobs.py) — some bundled
    jobs (e.g. backup-state) are deliberately registered disabled and must stay that way, so a
    state that matches what was intended is OK, not a permanent WARN."""
    from cc_session_tools.lib.scheduler import registry

    name = f"ccsched-job:{job_id}"
    try:
        specs = registry.load_registry()
    except registry.RegistryError as exc:
        return CheckResult(name=name, status=Status.WARN, reason=f"ccsched.db unreadable: {exc}")
    for spec in specs:
        if spec.job_id == job_id:
            if spec.enabled == expected_enabled:
                state_desc = "enabled" if spec.enabled else "disabled (as intended)"
                return CheckResult(name=name, status=Status.OK, reason=f"registered and {state_desc}")
            if expected_enabled:
                return CheckResult(
                    name=name, status=Status.WARN,
                    reason=f"registered but disabled — run 'ccsched enable {job_id}'",
                )
            return CheckResult(
                name=name, status=Status.WARN,
                reason=(
                    f"registered but enabled — this job must stay disabled, "
                    f"run 'ccsched disable {job_id}'"
                ),
            )
    return CheckResult(
        name=name, status=Status.WARN,
        reason="not registered — run 'ccst ccsched-jobs install --apply'",
    )
```

Update its call site (`doctor.py:695-696`) to pass each job's own expected state:

```python
    for job in bundled_jobs.BUNDLED_CCSCHED_JOBS:
        results.append(check_ccsched_job_registered(job.job_id, expected_enabled=job.enabled))
```

`expected_enabled` defaults to `True`, so the three existing tests
(`test_check_ccsched_job_registered_ok_when_present_and_enabled`,
`_warns_when_missing`, `_warns_when_disabled`) keep passing unchanged — they call the function
without the new keyword.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scheduler_bundled_jobs.py tests/test_ccst_ccsched_jobs_cli.py tests/test_ccst_doctor.py -v`
Expected: all pass, including the 3 existing `check_ccsched_job_registered` tests unchanged

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/bundled_jobs.py src/cc_session_tools/cli/ccst.py src/cc_session_tools/lib/doctor.py tests/test_scheduler_bundled_jobs.py tests/test_ccst_ccsched_jobs_cli.py tests/test_ccst_doctor.py
git commit -m "feat: register backup-state as a disabled bundled ccsched job"
```

---

### Task 3: `ccst backup-state` CLI command

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py` — parser (right after the `doctor` block, which runs
  `ccst.py:1586-1644`), handler (near `_cmd_doctor`), dispatch (near `ccst.py:2085-2086`)
- Create: `tests/test_ccst_backup_state_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ccst_backup_state_cli.py
from __future__ import annotations

import os
import sqlite3
import stat
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


def _write_stub(path: Path, exit_code: int) -> None:
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def base_env(tmp_path):
    env = os.environ.copy()
    env["CCST_DATA_HOME"] = str(tmp_path / "data-home")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _write_stub(fakebin / "pgrep", 1)  # 1 = no claude process found
    _write_stub(fakebin / "lsof", 1)  # 1 = nothing has it open
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    return env


def _write_fake_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()


def test_backup_state_copies_existing_stores(base_env, tmp_path):
    _write_fake_db(tmp_path / "data-home" / "sessions.db")
    r = _run(base_env, "backup-state")
    assert r.returncode == 0, r.stderr
    assert "backed up: sessions.db" in r.stdout
    backups = list((tmp_path / "data-home" / "state-backups").glob("*/sessions.db"))
    assert len(backups) == 1


def test_backup_state_reports_skipped_stores(base_env):
    r = _run(base_env, "backup-state")
    assert r.returncode == 0, r.stderr
    assert "skipped (not present yet)" in r.stdout
    assert "sessions.db" in r.stdout


def test_backup_state_records_job_success(base_env, monkeypatch):
    r = _run(base_env, "backup-state")
    assert r.returncode == 0, r.stderr
    monkeypatch.setenv("CCST_DATA_HOME", base_env["CCST_DATA_HOME"])
    from cc_session_tools.lib.scheduler import state
    job_state = state.get_state("backup-state")
    assert job_state is not None
    assert job_state.last_success is not None


def test_backup_state_refuses_when_claude_running(base_env, tmp_path):
    _write_stub(tmp_path / "fakebin" / "pgrep", 0)
    _write_fake_db(tmp_path / "data-home" / "sessions.db")
    r = _run(base_env, "backup-state")
    assert r.returncode == 1
    assert "claude" in r.stderr.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_backup_state_cli.py -v`
Expected: FAIL — `argument noun: invalid choice: 'backup-state'`

- [ ] **Step 3: Write the implementation**

Add the parser, right after the `doctor` block (`ccst.py:1586-1610` region, before `# ---- shell
----`):

```python
    # ---- backup-state ----
    sub.add_parser(
        "backup-state",
        help="Local-only backup of CCST's machine-state databases (spec §4.1, §5)",
    )
```

Add the handler near `_cmd_doctor`:

```python
def _cmd_backup_state(args: argparse.Namespace) -> int:
    from cc_session_tools.lib import backup_state

    try:
        result = backup_state.run_backup()
    except RuntimeError as exc:
        print(f"ccst backup-state: {exc}", file=sys.stderr)
        return 1
    if result.backed_up:
        print(f"backed up: {', '.join(result.backed_up)}")
    if result.skipped_missing:
        print(f"skipped (not present yet): {', '.join(result.skipped_missing)}")
    backup_state.record_job_success()
    return 0
```

Add dispatch, right after the `doctor` dispatch (`ccst.py:2085-2086`):

```python
    if args.noun == "backup-state":
        sys.exit(_cmd_backup_state(args))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_backup_state_cli.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_backup_state_cli.py
git commit -m "feat: add 'ccst backup-state' local-only backup command"
```

---

### Task 4: Wire the staleness nudge into `ccst pdata sync push`/`pull` (requires Plan 1)

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py` — `_cmd_pdata_sync_push`, `_cmd_pdata_sync_pull_project`,
  `_cmd_pdata_sync_pull_all` (all added by Plan 1)
- Modify: `tests/test_ccst_pdata_sync_cli.py` (added by Plan 1) — extend `base_env` with
  `CCST_DATA_HOME` isolation, add nudge tests

Do not start this task until Plan 1's `2026-08-02-multi-laptop-sync-01-core-sync.md` has landed —
these three functions don't exist before then.

- [ ] **Step 1: Write the failing tests**

```python
# modify tests/test_ccst_pdata_sync_cli.py's base_env fixture — add one line so the nudge's
# ccsched.db read is isolated from the developer's real machine state:

@pytest.fixture
def base_env(tmp_path):
    env = os.environ.copy()
    env["CCST_PROJECT_DB_DIR"] = str(tmp_path / "project-db")
    env["CCST_PROJECTS_ROOT"] = str(tmp_path / "projects")
    env["CCST_DATA_HOME"] = str(tmp_path / "data-home")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _write_stub(fakebin / "pgrep", 1)
    _write_stub(fakebin / "lsof", 1)
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    return env
```

```python
# append to tests/test_ccst_pdata_sync_cli.py

def test_sync_push_nudges_when_backup_state_never_ran(base_env):
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "notes", "--content", "x")
    r = _run(base_env, "pdata", "sync", "push", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "backup-state" in r.stderr.lower()


def test_sync_push_silent_when_backup_state_ran_recently(base_env):
    _run(base_env, "backup-state")
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "notes", "--content", "x")
    r = _run(base_env, "pdata", "sync", "push", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "backup-state" not in r.stderr.lower()


def test_sync_pull_project_nudges_when_backup_state_never_ran(base_env, tmp_path):
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "notes", "--content", "x")
    _run(base_env, "pdata", "sync", "push", "--project", "demo")
    (tmp_path / "project-db" / "demo.db").unlink()
    r = _run(base_env, "pdata", "sync", "pull", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "backup-state" in r.stderr.lower()


def test_sync_pull_all_nudges_once_when_backup_state_never_ran(base_env):
    _run(base_env, "pdata", "add", "--project", "alpha", "--group", "notes", "--content", "x")
    _run(base_env, "pdata", "sync", "push", "--project", "alpha")
    r = _run(base_env, "pdata", "sync", "pull", "--all-projects")
    assert r.returncode == 0, r.stderr
    assert r.stderr.lower().count("backup-state") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_pdata_sync_cli.py -k nudge -v`
Expected: FAIL — no nudge text on stderr (not wired in yet)

- [ ] **Step 3: Write the implementation**

Modify `_cmd_pdata_sync_push` to print the nudge after a successful push:

```python
def _cmd_pdata_sync_push(args: argparse.Namespace) -> int:
    from cc_session_tools.lib import backup_state
    from cc_session_tools.lib.pdata import sync

    try:
        snapshot = sync.create_snapshot(args.project)
    except ValueError as exc:
        print(f"ccst pdata sync push: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"ccst pdata sync push: {exc}", file=sys.stderr)
        return 1
    print(f"{args.project}: snapshot written to {snapshot.path}")
    nudge = backup_state.staleness_nudge()
    if nudge:
        print(f"ccst pdata sync push: {nudge}", file=sys.stderr)
    return 0
```

Modify `_cmd_pdata_sync_pull_project`, appending the same nudge right before its final `return 0`:

```python
    print(f"{args.project}: restored snapshot {chosen.timestamp}")
    _maybe_prune_snapshots(args.project, snapshots)
    from cc_session_tools.lib import backup_state
    nudge = backup_state.staleness_nudge()
    if nudge:
        print(f"ccst pdata sync pull: {nudge}", file=sys.stderr)
    return 0
```

Modify `_cmd_pdata_sync_pull_all`, appending the nudge once after the summary (not per-project —
matches spec §5's bulk-mode "no per-project cleanup prompt... too interruptive" precedent):

```python
    print(f"pulled: {', '.join(pulled) if pulled else '(none)'}")
    print(f"skipped (local newer): {', '.join(skipped) if skipped else '(none)'}")
    print(f"no snapshot found: {', '.join(not_found) if not_found else '(none)'}")
    from cc_session_tools.lib import backup_state
    nudge = backup_state.staleness_nudge()
    if nudge:
        print(f"ccst pdata sync pull: {nudge}", file=sys.stderr)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_pdata_sync_cli.py -v`
Expected: all pass (Plan 1's own tests plus this task's 4 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_pdata_sync_cli.py
git commit -m "feat: nudge towards 'ccst backup-state' from pdata sync push/pull"
```

---

### Task 5: Version bump and CHANGELOG

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

This is the last task of the last plan to land (per the overview's plan-index dependency table) —
only do this once Plans 1, 2, and this plan's Tasks 1-4 are all merged.

- [ ] **Step 1: Bump the version**

In `pyproject.toml:7`, change:

```toml
version = "1.4.1"
```

to:

```toml
version = "1.5.0"
```

- [ ] **Step 2: Cut the CHANGELOG section**

`CHANGELOG.md`'s `## [Unreleased]` section may already contain entries for other, unrelated
changes merged since the last release (check it — as of this plan being written it holds a
`bash-hard-deny` fix). **Do not discard those.** Add this feature's bullets into the existing
`## [Unreleased]` content (a new `### Added` subsection alongside whatever `### Fixed`/`###
Changed` subsections are already there), then rename the `## [Unreleased]` heading itself to
`## [1.5.0] - <today's date, YYYY-MM-DD>` — carrying every pre-existing entry forward into the
same release rather than deleting them. Finally, add a fresh, empty `## [Unreleased]` heading
above that. Follow the file's existing "bold lead phrase + why" bullet convention (see the
`## [1.4.1]` entry for the shape). Cover, at minimum:

- `ccst pdata sync push`/`pull` (single-project and `--all-projects`), with the running-session
  safety gate and staleness check
- `ccl`/`ccs` tagging sessions created on a different laptop; `ccr` refusing to resume them
  without `--force`
- `ccst backup-state` for local machine-state database backups

Add the new version's compare-link line at the bottom of the file, next to the existing
`[1.4.1]: ...` line, following the same `[X.Y.Z]: .../compare/vA...vB` pattern already there.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore: release 1.5.0 (multi-laptop sync)"
```

---

## Verification

- [ ] **Run the full test suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: all tests pass

- [ ] **Run the linter/type-checker if configured**

Run: `uv run ruff check src/ tests/` (and `uv run mypy src/` if configured)
Expected: no new errors introduced by this plan's files

- [ ] **Manual smoke test** (requires quitting any running Claude Code session first)

```bash
uv run python -m cc_session_tools.cli.ccst backup-state
uv run python -m cc_session_tools.cli.ccst ccsched-jobs install --apply
uv run python -m cc_session_tools.cli.ccsched show backup-state   # enabled: false
uv run python -m cc_session_tools.cli.ccst pdata sync push --project <some-real-project>
# should print a "consider running 'ccst backup-state'" nudge if it's been a while
```

## Handoff

Tasks 1-3 and 5 are independent of Plan 1/Plan 2. Task 4 requires Plan 1 to have landed first.
After Task 5, all three plans in this feature are complete — reinstall the global tool per this
repo's `.claude/CLAUDE.md` ("After merging a PR" section): `uv tool install --reinstall
~/repos/claude-code-session-tools`.
