# `ccst pdata init --write` usability follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `ccst pdata init --write` log end in an unambiguous `SUCCESS`/`ERROR: ...`
line, print the self-verify command on success, and give backups a human-readable filename —
three small, independent fixes found while completing `home`'s pdata migration on 2.7.1.

**Architecture:** `cli/ccst.py`'s `_cmd_pdata_init` gets an explicit sentinel `print()` at each of
its three known exit points inside the `with write_log.WriteLog(...)` block; `write_log.py`'s
`WriteLog.__exit__` gets a matching sentinel write for the fourth path (an exception that escapes
the block unhandled). `pdata/backup.py`'s `create_backup()` swaps `int(time.time())` for
`time.strftime('%Y%m%d-%H%M%S')` in the final archive filename.

**Tech Stack:** Python 3.13, pytest + `capsys`/subprocess-based CLI tests (matching existing
conventions in `tests/pdata/` and `tests/test_ccst_pdata_init_cli.py`).

Spec: `docs/superpowers/specs/2026-08-20-pdata-init-write-usability-design.md`

Work happens in a dedicated worktree per this repo's `.claude/CLAUDE.md`:
```sh
git worktree add .worktrees/pdata-init-write-usability -b f/20260820-pdata-init-write-usability
cd .worktrees/pdata-init-write-usability
uv sync --extra dev
```

---

### Task 1: Success/failure sentinel on the three known CLI exit paths

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py` (`_cmd_pdata_init`)
- Test: `tests/test_ccst_pdata_init_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_write_log_ends_with_success_sentinel_on_success(tmp_path, monkeypatch, ...):
    # run a real --write against a fixture project that succeeds
    ...
    log_text = (project_root / "ccst-pdata-init-write.log").read_text()
    assert log_text.rstrip().splitlines()[-1] == "SUCCESS"

def test_write_log_ends_with_error_sentinel_on_verification_failure(tmp_path, monkeypatch, ...):
    # force write_result.failure via the existing verification-failure fixture path
    ...
    log_text = (project_root / "ccst-pdata-init-write.log").read_text()
    assert log_text.rstrip().splitlines()[-1].startswith("ERROR: ")

def test_write_log_ends_with_error_sentinel_on_resolve_project_root_value_error(...):
    # existing fixture that raises ValueError from resolve_project_root / init_service.write
    ...
    assert log_text.rstrip().splitlines()[-1].startswith("ERROR: ")
```

Reuse whatever existing fixtures `tests/test_ccst_pdata_init_cli.py` already has for a clean
success run and for a verification-failure run — check that file first rather than building new
fixtures from scratch.

- [ ] **Step 2: Run to verify red**

Run: `uv run pytest tests/test_ccst_pdata_init_cli.py -k sentinel -v`

- [ ] **Step 3: Implement**

In `_cmd_pdata_init`, add immediately before each `return`:
- Before `return 2` (both `except` branches): `print(f"ERROR: {exc}")`.
- Before `return 1` (verification failure): `print(f"ERROR: verification failed ({len(write_result.failure.reasons)} reason(s))")`.
- Before `return 0` (success): `print("SUCCESS")`.

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/test_ccst_pdata_init_cli.py -k sentinel -v`

- [ ] **Step 5: Full pdata test suite**

Run: `uv run pytest tests/pdata/ tests/test_ccst_pdata_init_cli.py -q`

---

### Task 2: Sentinel on the fourth path — exception escaping `WriteLog`'s `with` block

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/write_log.py`
- Test: `tests/pdata/test_write_log.py`

- [ ] **Step 1: Write the failing test**

```python
def test_write_log_writes_error_sentinel_for_an_escaping_exception(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    log_path = project_root / write_log.LOG_FILENAME

    with pytest.raises(RuntimeError):
        with write_log.WriteLog(project_root):
            raise RuntimeError("boom")

    log_text = log_path.read_text()
    assert log_text.rstrip().splitlines()[-1] == "ERROR: RuntimeError: boom"
```

- [ ] **Step 2: Run to verify red** — `uv run pytest tests/pdata/test_write_log.py -k sentinel -v`

- [ ] **Step 3: Implement** — in `WriteLog.__exit__`, inside the existing
`if exc is not None:` block, after `traceback.print_exception(...)` and its flush, add (same
`try/except OSError: pass` wrapping already used there):

```python
try:
    self._file.write(f"ERROR: {exc_type.__name__}: {exc}\n")
    self._file.flush()
except OSError:
    pass
```

- [ ] **Step 4: Run to verify green, plus the full write_log suite**

Run: `uv run pytest tests/pdata/test_write_log.py -q`

- [ ] **Step 5: Confirm existing traceback-capture test (`test_write_log_captures_traceback_of_an_exception_that_escapes`) still passes unmodified** — the new write is appended after, not instead of, the traceback.

---

### Task 3: Print the self-verify command on success

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py` (`_cmd_pdata_init`, success branch)
- Test: `tests/test_ccst_pdata_init_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_write_success_prints_verify_command(...):
    result = run_ccst(["pdata", "init", "--project", "demo", "--write"])
    assert "ccst pdata verify --project demo --full" in result.stdout
```

- [ ] **Step 2: Run to verify red**

- [ ] **Step 3: Implement** — after `print(write_result.report)`, add:

```python
print(f"\nVerify: ccst pdata verify --project {args.project} --full")
```

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/test_ccst_pdata_init_cli.py -k verify_command -v`

---

### Task 4: Human-readable backup filenames

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/backup.py`
- Test: `tests/pdata/test_backup.py`

- [ ] **Step 1: Write the failing test**

```python
def test_create_backup_uses_readable_timestamp_filename(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    assert re.fullmatch(r"demo-\d{8}-\d{6}\.tar\.gz", tar_path.name)
```

- [ ] **Step 2: Run to verify red** — `uv run pytest tests/pdata/test_backup.py -k readable -v`

- [ ] **Step 3: Implement** — in `create_backup`, change the `final_path` line to:

```python
final_path = target_dir / f"{project}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
```

- [ ] **Step 4: Run to verify green, plus the existing `demo-*.tar.gz`-glob tests still pass**

Run: `uv run pytest tests/pdata/test_backup.py -q`

---

### Task 5: Full verification + docs

- [ ] Run: `uv run pytest -q` — full suite green.
- [ ] Run: `uv run ruff check . && uv run mypy` (or whatever this repo's configured lint/type
  commands are — check `pyproject.toml` / CI workflow if unsure).
- [ ] Add a CHANGELOG.md entry (patch-level: bug-fix/polish, no interface change per this repo's
  version policy).
- [ ] Update any doc that quotes the old `<project>-<epoch>.tar.gz` filename format (grep for it
  first — `docs/`, `README.md`, docstrings).

### Task 6: Ship

Follow superpowers:finishing-a-development-branch once all tasks are green.
