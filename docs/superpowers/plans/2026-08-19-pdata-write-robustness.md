# pdata --write Robustness + Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ccst pdata init --write` never crash with a raw traceback, never leave a
corrupt file at a real backup's filename, back up the project's `.db` alongside `project_root`,
and stream/log its progress so a long-running or failing `--write` is diagnosable.

**Architecture:** `lib/pdata/backup.py` gets a temp-then-atomic-rename write path with bounded
retry on transient `OSError` and a sqlite3-backup-API snapshot of the project `.db` folded into
the same tar. `lib/pdata/init_service.py`'s `write()` gets a shared `_rollback()` helper reused
by both the existing verify-failure path and a new backup-failure path (via an `on_progress`
callback for phase-boundary messages, printed only by the CLI). A new `lib/pdata/write_log.py`
tees `sys.stdout`/`sys.stderr` into a per-run, flush-on-every-write log file for the duration of
`--write`, including any exception's traceback.

**Tech Stack:** Python 3.13, stdlib `tarfile`/`sqlite3`/`os.replace`, pytest + subprocess-based
CLI tests (matching existing conventions in `tests/pdata/` and `tests/test_ccst_pdata_init_cli.py`).

Spec: `docs/superpowers/specs/2026-08-19-pdata-write-robustness-design.md`
Diagnosis: `cc-sessions/20260819-improve-pdata-setup/working/2026-08-19-pdata-write-diagnosis.md`

Work happens in a dedicated worktree per this repo's `.claude/CLAUDE.md`:
```sh
git worktree add .worktrees/pdata-write-robustness -b f/20260819-pdata-write-robustness
cd .worktrees/pdata-write-robustness
uv sync --extra dev
```

---

### Task 1: `BackupError` + atomic temp-then-rename write in `backup.py`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/backup.py`
- Test: `tests/pdata/test_backup.py`

- [ ] **Step 1: Write the failing test — atomic rename, no partial file at the final name**

```python
def test_create_backup_leaves_no_tmp_file_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    assert tar_path.exists()
    tmp_candidates = list(tar_path.parent.glob("*.tmp"))
    assert tmp_candidates == []
```

- [ ] **Step 2: Run it to verify it fails or (likely) passes trivially**

Run: `uv run pytest tests/pdata/test_backup.py::test_create_backup_leaves_no_tmp_file_on_success -v`

This one may already pass against the current implementation (there's no tmp file at all
today) — that's fine, it's a regression guard for the next step, not a red/green pivot on its
own. The real red/green pivot is Step 3's test.

- [ ] **Step 3: Write the failing test — a mid-write failure never leaves a corrupt file at the real name**

```python
def test_create_backup_leaves_no_partial_file_at_final_name_on_failure(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    monkeypatch.setattr(backup.time, "sleep", lambda _seconds: None)
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    def always_fails(self, name, **kwargs):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(tarfile.TarFile, "add", always_fails)

    with pytest.raises(backup.BackupError, match="demo"):
        backup.create_backup(project="demo", project_root=project_root)

    backups_dir = tmp_path / "backups"
    assert list(backups_dir.glob("demo-*.tar.gz")) == []
    assert list(backups_dir.glob("*.tmp")) == []
```

Add `import pytest` to the top of `tests/pdata/test_backup.py` alongside the existing imports.

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/pdata/test_backup.py::test_create_backup_leaves_no_partial_file_at_final_name_on_failure -v`
Expected: FAIL — `backup` module has no `BackupError` attribute (`AttributeError`), and today's
implementation writes straight to the final path so a failed `tar.add()` leaves a truncated
file right there.

- [ ] **Step 5: Implement — temp path, atomic rename, bounded retry, `BackupError`**

Replace the whole file:

```python
"""Pre-cutover backup snapshot for `ccst pdata init` (spec §7.1 step 5)."""
from __future__ import annotations

import os
import tarfile
import time
from pathlib import Path

from cc_session_tools.lib import paths

BACKUP_DIR_ENV = "CCST_PDATA_BACKUP_DIR"

# Transient I/O errors on network/DrvFS-backed project roots (e.g. a OneDrive-synced path
# mounted into WSL2) are often momentary — retry a few times before giving up rather than
# crashing the whole --write run on the first blip.
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (1, 2)


class BackupError(Exception):
    """create_backup() exhausted its retries against a persistent OSError."""


def backup_dir() -> Path:
    override = os.environ.get(BACKUP_DIR_ENV)
    return Path(override).expanduser() if override else paths.data_home() / "pdata-backups"


def create_backup(*, project: str, project_root: Path) -> Path:
    """tar.gz snapshot of project_root as it stands right now, written outside
    project_root (spec §7.1 step 5 — "stored outside the project folder, before
    touching any original file"). One backup per --write invocation; never
    overwritten or extended incrementally, matching the big-bang migration
    philosophy (spec goals).

    Written to a temp path first and atomically renamed to the final
    `<project>-<epoch>.tar.gz` name only once the archive is fully and successfully written —
    a failed or interrupted run never leaves a corrupt file at the name a valid backup would
    use. Transient OSErrors (e.g. a flaky network/DrvFS-backed project_root) are retried a
    bounded number of times before raising BackupError.
    """
    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / f"{project}-{int(time.time())}.tar.gz"
    tmp_path = final_path.parent / (final_path.name + ".tmp")

    last_exc: OSError | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                tar.add(project_root, arcname=project)
            os.replace(tmp_path, final_path)
            return final_path
        except OSError as exc:
            last_exc = exc
            tmp_path.unlink(missing_ok=True)
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
    raise BackupError(
        f"backup of {project!r} failed after {_MAX_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/pdata/test_backup.py -v`
Expected: all PASS, including the three pre-existing tests (`test_backup_dir_env_override`,
`test_create_backup_writes_tar_containing_project_files`,
`test_create_backup_is_outside_project_root`).

- [ ] **Step 7: Write + pass the retry-then-succeed test**

```python
def test_create_backup_retries_transient_os_error_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    monkeypatch.setattr(backup.time, "sleep", lambda _seconds: None)
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    calls = {"n": 0}
    real_add = tarfile.TarFile.add

    def flaky_add(self, name, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(5, "Input/output error")
        return real_add(self, name, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "add", flaky_add)

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    assert calls["n"] == 2
    assert tar_path.exists()
```

Run: `uv run pytest tests/pdata/test_backup.py::test_create_backup_retries_transient_os_error_then_succeeds -v`
Expected: PASS against the Step 5 implementation already (no further code change needed —
this step just adds coverage for the retry path the implementation already has).

- [ ] **Step 8: Commit**

```bash
git add src/cc_session_tools/lib/pdata/backup.py tests/pdata/test_backup.py
git commit -m "fix(pdata): make create_backup atomic and retry transient OSError"
```

---

### Task 2: Fold the project's `.db` into the backup

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/backup.py`
- Test: `tests/pdata/test_backup.py`

- [ ] **Step 1: Write the failing test**

```python
def test_create_backup_includes_project_db_snapshot(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import store

    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "dbs"))
    project_root = tmp_path / "demo"
    project_root.mkdir()

    db_path = store.db_path("demo")
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    with tarfile.open(tar_path, "r:gz") as tar:
        assert "demo/demo.db" in tar.getnames()
        extracted = tmp_path / "extracted.db"
        with tar.extractfile("demo/demo.db") as fh:
            extracted.write_bytes(fh.read())

    verify_conn = sqlite3.connect(extracted)
    assert verify_conn.execute("SELECT x FROM t").fetchall() == [(1,)]


def test_create_backup_skips_db_entry_when_no_db_exists(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import store

    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "dbs"))
    project_root = tmp_path / "demo"
    project_root.mkdir()

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    with tarfile.open(tar_path, "r:gz") as tar:
        assert "demo/demo.db" not in tar.getnames()
```

Add `import sqlite3` to the test file's imports.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pdata/test_backup.py::test_create_backup_includes_project_db_snapshot -v`
Expected: FAIL — `demo/demo.db` not in `tar.getnames()`.

- [ ] **Step 3: Implement — sqlite3 backup-API snapshot added to the same tar**

In `backup.py`, add the import and a helper, and call it from inside the `tarfile.open` block:

```python
import sqlite3
...
from cc_session_tools.lib import db
from cc_session_tools.lib.pdata import store
```

```python
def _snapshot_db_into_tar(tar: tarfile.TarFile, *, project: str, tmp_dir: Path) -> None:
    """Add a point-in-time consistent copy of the project's .db to the archive, via the
    sqlite3 backup API rather than a raw file copy — a raw copy of a WAL-mode database can
    miss rows still sitting in the -wal file that haven't been checkpointed into the main
    file yet. No-op if the project has no .db yet (a brand-new project's first --write).

    The source connection goes through the shared lib/db.py helper, readonly, matching this
    repo's data-store convention (WAL + busy-timeout applied consistently). The destination
    is a throwaway temp file immediately tar'd and deleted, not a persistent store, so it
    connects directly via sqlite3.connect() — routing it through the WAL-enabling helper
    would just leave -wal/-shm sidecars to clean up around a file that exists for a few
    lines and is never reopened."""
    db_source = store.db_path(project)
    if not db_source.exists():
        return
    snapshot_path = tmp_dir / f"{project}.db"
    try:
        src_conn = db.connect(db_source, readonly=True)
        try:
            dst_conn = sqlite3.connect(snapshot_path)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
        tar.add(snapshot_path, arcname=f"{project}/{project}.db")
    finally:
        snapshot_path.unlink(missing_ok=True)
```

And in `create_backup()`'s retry loop, replace the `with tarfile.open(...)` body:

```python
        try:
            with tarfile.open(tmp_path, "w:gz") as tar:
                _snapshot_db_into_tar(tar, project=project, tmp_dir=target_dir)
                tar.add(project_root, arcname=project)
            os.replace(tmp_path, final_path)
            return final_path
```

`target_dir` is already created (`mkdir(parents=True, exist_ok=True)` a few lines up) and is
outside `project_root`, so the snapshot's own temp file never gets picked up by
`tar.add(project_root, ...)` on the same or a later line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pdata/test_backup.py -v`
Expected: all PASS, including both new tests and everything from Task 1.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/backup.py tests/pdata/test_backup.py
git commit -m "feat(pdata): back up the project .db alongside project_root before cutover"
```

---

### Task 3: Extract `_rollback()` and catch `BackupError` in `init_service.write()`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/init_service.py`
- Test: `tests/pdata/test_init_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_write_rolls_back_and_reports_failure_when_backup_raises(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import backup, service

    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\nsecond\n")

    init_service.dry_run(project="demo")

    def _always_fails(**kwargs):
        raise backup.BackupError("simulated backup failure")

    monkeypatch.setattr(backup, "create_backup", _always_fails)

    result = init_service.write(project="demo")

    assert result.failure is not None
    assert any("simulated backup failure" in reason for reason in result.failure.reasons)
    assert service.list_records(project="demo", record_group="ideas") == []
    # Nothing was cut over — source file untouched, no .pdata-migrated dir.
    assert (project_dir / "ideas.csv").exists()
    assert not (project_dir / init_paths.MIGRATED_ARCHIVE_DIRNAME).exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pdata/test_init_service.py::test_write_rolls_back_and_reports_failure_when_backup_raises -v`
Expected: FAIL — `backup.BackupError` propagates uncaught out of `write()` today (the bug this
whole plan exists to fix).

- [ ] **Step 3: Implement — extract `_rollback`, catch `BackupError` around the backup call**

In `init_service.py`, add a module-level helper (place it right before `write()`):

```python
def _rollback(*, project: str, created_ids: list[int]) -> list[str]:
    """Soft-delete every id in created_ids (spec §4.5) — no hard delete, full auditability.
    Every id here was inserted earlier in this single-threaded run, so its version is always
    1. Each delete_record call is wrapped individually: service.RecordNotFoundError/
    VersionConflictError are plain Exception subclasses (not ValueError/OSError), so an
    unwrapped raise here would abort the loop mid-way and leave some just-inserted rows
    soft-deleted and others still live. Any rollback failure is returned alongside the
    caller's own failure reasons rather than raised, since the caller still needs a
    WriteResult back, not a crash."""
    rollback_failures: list[str] = []
    for record_id in created_ids:
        try:
            service.delete_record(project=project, record_id=record_id, expected_version=1)
        except (service.RecordNotFoundError, service.VersionConflictError) as exc:
            rollback_failures.append(f"record {record_id}: rollback failed: {exc}")
    return rollback_failures
```

Replace the `if reasons:` block inside `write()` (the block that currently builds
`rollback_failures` inline) with:

```python
        if reasons:
            return WriteResult(
                created_record_ids=[], entries_written=[], backup_path=None,
                failure=WriteFailure(reasons=reasons + _rollback(project=project, created_ids=created_ids)),
            )

        # Still inside both overrides: a rehearsed run's backup must land in the
        # rehearsal sandbox (backup_dir_override), never in the real backup dir.
        try:
            backup_path = backup.create_backup(project=project, project_root=project_root)
        except backup.BackupError as exc:
            return WriteResult(
                created_record_ids=[], entries_written=[], backup_path=None,
                failure=WriteFailure(
                    reasons=[str(exc)] + _rollback(project=project, created_ids=created_ids)
                ),
            )
```

Delete the now-unused inline `rollback_failures` loop that used to precede the `return` in the
`if reasons:` block — it's fully replaced by the `_rollback()` call above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pdata/test_init_service.py -v`
Expected: all PASS, including the new test, `test_write_reports_rollback_failure_without_crashing`
(now exercising `_rollback` indirectly), and every other existing `write()` test.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/init_service.py tests/pdata/test_init_service.py
git commit -m "fix(pdata): catch backup failures in write() and roll back cleanly"
```

---

### Task 4: Phase-progress callback in `init_service.write()`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/init_service.py`
- Test: `tests/pdata/test_init_service.py`

- [ ] **Step 1: Write the failing test**

```python
def test_write_reports_progress_through_on_progress_callback(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\n")

    init_service.dry_run(project="demo")
    messages: list[str] = []
    result = init_service.write(project="demo", on_progress=messages.append)

    assert result.failure is None
    joined = "\n".join(messages)
    assert "ideas.csv" in joined
    assert any("verif" in m.lower() for m in messages)
    assert any("backup" in m.lower() for m in messages)
    assert any("cut" in m.lower() or "cutover" in m.lower() for m in messages)


def test_write_on_progress_defaults_to_silent(monkeypatch, tmp_path):
    """Existing callers (and every test above this one) call write() with no on_progress —
    must keep working exactly as before, silently."""
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "dbs"))
    monkeypatch.setenv("CCST_PDATA_BACKUP_DIR", str(tmp_path / "backups"))
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\n")

    init_service.dry_run(project="demo")
    result = init_service.write(project="demo")  # no on_progress kwarg
    assert result.failure is None
```

- [ ] **Step 2: Run to verify the first test fails**

Run: `uv run pytest tests/pdata/test_init_service.py::test_write_reports_progress_through_on_progress_callback -v`
Expected: FAIL — `write()` has no `on_progress` parameter yet (`TypeError`).

- [ ] **Step 3: Implement — `on_progress` kwarg + `_emit` calls at phase boundaries**

Add near the top of `init_service.py`:

```python
from collections.abc import Callable
```

Add a tiny helper (next to `_rollback`):

```python
def _emit(on_progress: Callable[[str], None] | None, message: str) -> None:
    if on_progress is not None:
        on_progress(message)
```

Change `write()`'s signature:

```python
def write(
    *, project: str, rehearse: Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> WriteResult:
```

Add `_emit(...)` calls at these points inside `write()`:

- Right before the `for entry in m.entries:` loop:
  ```python
        db_owned = [e for e in m.entries if e.classification == "db-owned"]
        _emit(on_progress, f"Importing {len(db_owned)} file(s)...")
  ```
  (keep the existing `for entry in m.entries:` loop as-is below this — it already re-filters
  on `entry.classification != "db-owned": continue`, so `db_owned` here is purely for the
  progress message's count, not a behavior change to the loop itself.)
- Inside the loop, right after `try:` and before `for spec in entry.fields:`:
  ```python
                _emit(on_progress, f"  importing {entry.path} -> group={entry.db_group()}...")
  ```
- Right after `entry_rows[entry.path] = rows_for_entry` (still inside the `try` block, before
  `written_entries.append(entry)`):
  ```python
                _emit(on_progress, f"    {len(rows_for_entry)} row(s) imported")
```
- Right before the `reasons.extend(_verify(...))` call:
  ```python
        _emit(on_progress, "Verifying imported rows...")
  ```
- Right before the `try:` that wraps `backup.create_backup(...)`:
  ```python
        _emit(on_progress, "Backing up project and database before cutover...")
  ```
- Right after a successful `backup_path = backup.create_backup(...)` (inside the `try`, after
  the assignment, before the `except` block ends the `try/except`):
  ```python
        _emit(on_progress, f"Backup written: {backup_path}")
  ```
- Right before `cutover.archive_entries(...)` (after the `with (...)` block has exited):
  ```python
    _emit(on_progress, f"Cutting over {len(written_entries)} file(s)...")
  ```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pdata/test_init_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/init_service.py tests/pdata/test_init_service.py
git commit -m "feat(pdata): emit phase-progress messages from write() via on_progress"
```

---

### Task 5: `write_log.WriteLog` — tee stdout/stderr to a flushed per-run log file

**Files:**
- Create: `src/cc_session_tools/lib/pdata/write_log.py`
- Test: `tests/pdata/test_write_log.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for lib/pdata/write_log.py."""
from __future__ import annotations

import sys

from cc_session_tools.lib.pdata import write_log


def test_write_log_tees_stdout_into_the_log_file(tmp_path, capsys):
    project_root = tmp_path / "demo"
    project_root.mkdir()

    with write_log.WriteLog(project_root):
        print("hello from stdout")

    captured = capsys.readouterr()
    assert "hello from stdout" in captured.out

    log_path = project_root / write_log.LOG_FILENAME
    assert "hello from stdout" in log_path.read_text()


def test_write_log_flushes_after_every_write(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    log_path = project_root / write_log.LOG_FILENAME

    with write_log.WriteLog(project_root):
        print("first line")
        # Read the file from a second, independent handle while the first is still open —
        # only possible if the write above was actually flushed to disk already.
        assert "first line" in log_path.read_text()


def test_write_log_captures_traceback_of_an_exception_that_escapes(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    log_path = project_root / write_log.LOG_FILENAME

    try:
        with write_log.WriteLog(project_root):
            raise ValueError("boom")
    except ValueError:
        pass

    content = log_path.read_text()
    assert "ValueError" in content
    assert "boom" in content


def test_write_log_restores_real_stdout_and_stderr_on_exit(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    real_stdout, real_stderr = sys.stdout, sys.stderr

    with write_log.WriteLog(project_root):
        assert sys.stdout is not real_stdout

    assert sys.stdout is real_stdout
    assert sys.stderr is real_stderr


def test_write_log_truncates_across_successive_runs(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    log_path = project_root / write_log.LOG_FILENAME

    with write_log.WriteLog(project_root):
        print("first run, a long line of output")

    with write_log.WriteLog(project_root):
        print("second run")

    content = log_path.read_text()
    assert "second run" in content
    assert "first run" not in content
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pdata/test_write_log.py -v`
Expected: FAIL — `cc_session_tools.lib.pdata.write_log` doesn't exist (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
"""Tee stdout/stderr to a per-run log file during `ccst pdata init --write`.

--write can run for a long time with zero visible progress otherwise, and a failure can
crash before any of it is reported anywhere durable. WriteLog gives every --write invocation
a flushed-per-line, truncate-per-run log file inside the project, capturing everything the
CLI prints plus the traceback of anything that escapes unhandled.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from types import TracebackType

LOG_FILENAME = "ccst-pdata-init-write.log"


class _Tee:
    """File-like object writing to two streams, flushing the log stream after every write so
    the file is never left holding a buffered-but-unflushed tail if the process dies (crash,
    OOM-kill, Ctrl-C) mid-run."""

    def __init__(self, real_stream, log_file) -> None:
        self._real = real_stream
        self._log = log_file

    def write(self, data: str) -> int:
        self._real.write(data)
        self._log.write(data)
        self._log.flush()
        return len(data)

    def flush(self) -> None:
        self._real.flush()
        self._log.flush()

    def isatty(self) -> bool:
        return False


class WriteLog:
    """Truncates project_root/ccst-pdata-init-write.log at the start of every --write run —
    one log per invocation, matching the tool's big-bang/one-shot semantics; stitching
    together unrelated attempts into one file would be confusing, not helpful — and tees
    every sys.stdout/sys.stderr write into it for the duration of the `with` block. Any
    exception that escapes the block has its full traceback written to the log before the
    real streams are restored, so it's captured even though Python's own default unhandled-
    exception printer runs after __exit__."""

    def __init__(self, project_root: Path) -> None:
        self._path = project_root / LOG_FILENAME
        self._file = None
        self._real_stdout = None
        self._real_stderr = None

    def __enter__(self) -> WriteLog:
        self._file = open(self._path, "w", buffering=1)
        self._real_stdout, self._real_stderr = sys.stdout, sys.stderr
        sys.stdout = _Tee(self._real_stdout, self._file)
        sys.stderr = _Tee(self._real_stderr, self._file)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc is not None:
            traceback.print_exception(exc_type, exc, tb, file=self._file)
            self._file.flush()
        sys.stdout, sys.stderr = self._real_stdout, self._real_stderr
        self._file.close()
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pdata/test_write_log.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/write_log.py tests/pdata/test_write_log.py
git commit -m "feat(pdata): add WriteLog to tee --write's stdout/stderr into a per-run log file"
```

---

### Task 6: Wire `WriteLog` and progress printing into the CLI

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py:1046-1082` (`_cmd_pdata_init`)
- Test: `tests/test_ccst_pdata_init_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_pdata_init_write_streams_progress_and_writes_log_file(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\nsecond\n")
    base_env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")

    _run(base_env, "pdata", "init", "--project", "demo")
    r_write = _run(base_env, "pdata", "init", "--project", "demo", "--write")

    assert r_write.returncode == 0, r_write.stderr
    assert "Importing 1 file(s)" in r_write.stdout
    assert "Backing up" in r_write.stdout
    assert "Cutting over" in r_write.stdout

    log_path = project_dir / "ccst-pdata-init-write.log"
    assert log_path.exists()
    log_content = log_path.read_text()
    assert "Importing 1 file(s)" in log_content
    assert "Wrote 2 record(s)" in log_content


def test_pdata_init_write_log_captures_verification_failure(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text("doc_path,note\n/etc/passwd,bad\n")
    base_env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")

    _run(base_env, "pdata", "init", "--project", "demo")
    proposal_path = project_dir / ".ccst-pdata-proposal.json"
    data = json.loads(proposal_path.read_text())
    data["entries"][0]["file_path_column"] = "doc_path"
    proposal_path.write_text(json.dumps(data))

    r_write = _run(base_env, "pdata", "init", "--project", "demo", "--write")

    assert r_write.returncode == 1
    log_content = (project_dir / "ccst-pdata-init-write.log").read_text()
    assert "verification failed" in log_content.lower()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ccst_pdata_init_cli.py::test_pdata_init_write_streams_progress_and_writes_log_file -v`
Expected: FAIL — no `ccst-pdata-init-write.log` file is written today, and the CLI is silent
until it prints the final "Wrote N record(s)" summary.

- [ ] **Step 3: Implement — wire `WriteLog` and `on_progress=print` into `_cmd_pdata_init`**

Replace `_cmd_pdata_init` (`src/cc_session_tools/cli/ccst.py:1046-1082`) with:

```python
def _cmd_pdata_init(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import init_paths, init_service, write_log

    rehearse = Path(args.rehearse) if args.rehearse else None

    if not args.write:
        try:
            result = init_service.dry_run(project=args.project, rehearse=rehearse)
        except ValueError as exc:
            print(f"ccst pdata: {exc}", file=sys.stderr)
            return 2
        print(result.report)
        print(f"\nProposal: {result.proposal_path}")
        return 0

    try:
        project_root = init_paths.resolve_project_root(args.project, rehearse=rehearse)
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2

    with write_log.WriteLog(project_root):
        try:
            write_result = init_service.write(
                project=args.project, rehearse=rehearse, on_progress=print,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"ccst pdata: {exc}", file=sys.stderr)
            return 2

        if write_result.failure is not None:
            print("ccst pdata init: verification failed, nothing was cut over:", file=sys.stderr)
            for reason in write_result.failure.reasons:
                print(f"  - {reason}", file=sys.stderr)
            return 1

        print(
            f"Wrote {len(write_result.created_record_ids)} record(s) across "
            f"{len(write_result.entries_written)} file(s)."
        )
        print(f"Backup: {write_result.backup_path}")
        for path in write_result.entries_written:
            print(f"  cut over: {path}")
        print()
        print(write_result.report)  # spec §7.1 step 4's diff report, for review
        return 0
```

The `--rehearse` path is unaffected: `resolve_project_root` already returns the rehearsal path
when `rehearse` is set, so a rehearsed `--write` writes its log inside the rehearsal sandbox,
not the real project — consistent with `project_db_dir_override`/`backup_dir_override`'s
existing rehearsal isolation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_pdata_init_cli.py -v`
Expected: all PASS, including every pre-existing test in this file.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -q`
Expected: all PASS, zero regressions anywhere else in the repo.

- [ ] **Step 6: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_pdata_init_cli.py
git commit -m "feat(pdata): stream --write progress to stdout and a per-run log file"
```

---

### Task 7: Version bump, changelog, doctor/test sweep

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump the version**

In `pyproject.toml`, change:
```toml
version = "2.6.0"
```
to:
```toml
version = "2.7.0"
```

Minor bump per this repo's version policy (`.claude/CLAUDE.md`): new capability (the `.db`
backup, the `ccst-pdata-init-write.log` file and stdout progress stream) plus a bugfix
(backup-failure crash → clean `WriteFailure`), fully backwards-compatible — no existing
on-disk data becomes unreadable by old code, no flag or return-shape change for any existing
caller (`on_progress` is a new optional kwarg, default `None` preserves today's silent
behavior).

- [ ] **Step 2: Add the changelog entry**

In `CHANGELOG.md`, replace the empty `## [Unreleased]` section with:

```markdown
## [Unreleased]

## [2.7.0] - 2026-08-19

### Fixed

- **`ccst pdata init --write`** no longer crashes with a raw traceback if its pre-cutover
  backup step hits an I/O error (e.g. a transient failure reading a network- or
  DrvFS-backed project root). The failure is now retried a bounded number of times, and if
  it still fails, every row inserted during that run is rolled back and reported as a
  structured `WriteFailure` — matching the existing verification-failure behavior — instead
  of leaving a half-completed migration (DB rows committed, cutover never run) with no
  non-crash signal.
- The backup tar.gz is now written to a temp path and atomically renamed to its final name
  only on full success, so an interrupted backup can never leave a corrupt file sitting at
  the filename a valid backup would use.

### Added

- **`ccst pdata init --write`** now backs up the project's `.db` (via the sqlite3 backup API,
  for a point-in-time consistent snapshot even under WAL mode) alongside the existing
  `project_root` tar, inside the same archive, before cutover touches anything.
- **`ccst pdata init --write`** streams phase-by-phase progress to stdout (import / verify /
  backup / cutover) instead of producing no output until it finishes or crashes, and writes
  everything printed — plus the traceback of anything that still escapes unhandled — to a new
  `ccst-pdata-init-write.log` file inside the project, flushed after every line.
```

- [ ] **Step 3: Full verification sweep**

Run, in order, and confirm each exits 0:
```bash
uv run pytest -q
uv run mypy src/
uv run ruff check .
uv run ruff format --check .
```
(Use whichever of these commands this repo's CI config (`.github/workflows/`) actually runs —
check `ci.yml` for the exact invocation and flags before running, and use that instead if it
differs from the above.)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump to 2.7.0 for pdata --write robustness + logging"
```

---

## Out of scope (deliberately, per spec)

- A pre-flight concurrent-access check before `--write` starts mutating anything. WAL mode +
  `busy_timeout` (already set via the shared `lib/db.py` connection helper) means a second
  concurrent writer blocks-and-retries rather than corrupting anything — this is a UX
  nice-to-have (a friendlier fail-fast message instead of a silent block), not a correctness
  gap. Left as a TODO.md backlog item rather than built here.

## Manual follow-up (not part of this plan — yours to run)

Once this ships and you've reinstalled (`uv tool install --reinstall
~/repos/claude-code-session-tools`), restore `home`'s DB to its pre-migration state and re-run
the migration using the runbook already prepared:
`cc-sessions/20260819-improve-pdata-setup/out/2026-08-19-home-restore-runbook.md`.
