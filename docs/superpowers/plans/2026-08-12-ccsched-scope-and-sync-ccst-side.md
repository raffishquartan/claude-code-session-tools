# ccsched job scope + CCCS manifest sync (ccst side) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give ccsched jobs a `scope` (local vs cross-machine), and give `ccst` the two commands
that move cross-machine job definitions between a machine's `ccsched.db` and the
`claude-code-config-sync` (CCCS) manifest: `ccsched-jobs export` (local → manifest JSON) and
`ccsched-jobs sync --apply` (manifest → local, update-allowed).

**Architecture:** `scope` is an additive field on `JobSpec`/`BundledJob` defaulting to `local`, so
every existing job and every existing DB row keeps its current behaviour with no migration action
required (the `jobs` table gains a `scope TEXT NOT NULL DEFAULT 'local'` column via the existing
idempotent `_migrate_jobs_table` hook). The CCCS manifest's JSON shape gets exactly one
implementation — a new `lib/scheduler/manifest.py` — used by both the export and the sync command,
so the two can never drift. Machine-specific values in a cross-machine job's argv travel as
`{{PLACEHOLDER}}` tokens resolved at sync time from a machine-local, non-git-tracked values file
(`lib/scheduler/machine_values.py`); an unresolvable placeholder is a hard error naming the key,
never a silent skip or invented default.

**Tech Stack:** Python 3.11+, stdlib only (`argparse`, `json`, `sqlite3`, `re`, `dataclasses`,
`enum`, `pathlib`), pytest. Run everything with `uv run` from inside this worktree — never
`uv tool install` (see `.claude/CLAUDE.md`).

**Spec:** `docs/superpowers/specs/2026-08-12-ccsched-cross-machine-jobs-design.md` — this plan
covers §1, §3, §4 and §6 only. §2 and §5 are bash, live in the `claude-code-config-sync` repo,
and are implemented separately.

---

## Scope boundary — what this plan does NOT do

- No changes to `claude-code-config-sync` (§2 manifest file, §5 `check-config-drift.sh`).
- No new `ccst doctor` check. Spec §5 is explicit: `ccst doctor` has no role in cross-machine job
  drift detection; that surface is CCCS's session-start digest.
- No `git` invocation of any kind. Export prints to stdout; the operator commits by hand.
- No reverse-templating on export (spec §4: out of scope for v1).
- `scope` does not affect job execution, due-time computation, the digest, or the worker. It is
  metadata that only `ccsched show`, `export` and `sync` read.

---

## Decisions taken while planning

These resolve points the spec left implicit. Each is implemented and tested below.

1. **`enabled` is local-only state, never carried by the manifest.** The manifest shape is
   `BundledJob`'s (spec §2), and `BundledJob` has no `enabled` field — `ccsched-jobs install`
   hardcodes `enabled=True`. So `sync` uses `enabled=True` when *adding* a job, **preserves the
   local value** when *updating* one, and excludes `enabled` from its differs-or-not comparison.
   Otherwise a `ccsched disable`d job would silently re-enable on the next sync.
2. **`export` refuses a local-scope job** (exit 2, message telling the operator to run
   `ccsched edit <id> --scope cross-machine` first). The manifest holds only cross-machine jobs
   (§2); a `"scope": "local"` entry pasted into it would be a silent no-op at sync time.
3. **Manifest entries must state `"scope": "cross-machine"` explicitly.** Not defaulted. `export`
   always emits it, so this is never hand-typed, and it makes export output exactly what sync
   consumes (there is a round-trip test for this).
4. **A manifest entry whose id collides with a bundled job id is a hard error**, not a silent
   skip. Spec §6 requires bundled jobs stay on the `ccsched-jobs install` path; silently ignoring
   a colliding entry would leave the operator believing a sync applied when it did not.
5. **`sync` dry-run exits 0** even when drift exists, matching `ccsched-jobs install`. Drift
   *detection* is CCCS's bash hook (§5), not this command's exit code.
6. **Missing machine-values file is not itself an error** — only an unresolved placeholder is. A
   cross-machine job with no placeholders must sync fine on a machine that has never created the
   file.
7. **`scope` is shown by `ccsched show` only**, not added to `ccsched list`'s columns. Spec §1
   names `show`; `list` is a fixed-width table already at six columns.

---

## File structure

**Create:**

| Path | Responsibility |
|---|---|
| `src/cc_session_tools/lib/scheduler/machine_values.py` | Load the machine-local values file; resolve/validate `{{PLACEHOLDER}}` tokens in an argv. Pure logic split from the one file read. |
| `src/cc_session_tools/lib/scheduler/manifest.py` | The single implementation of the CCCS manifest JSON shape: `job_to_manifest_entry` (serialise) and `parse_manifest` (parse + validate + build `JobSpec`s). Both `export` and `sync` go through it. |
| `tests/scheduler/test_machine_values.py` | Unit tests for the above. |
| `tests/scheduler/test_manifest.py` | Unit tests for the above. |

**Modify:**

| Path | Change |
|---|---|
| `src/cc_session_tools/lib/scheduler/jobspec.py` | Add `JobScope`, `JobSpec.scope`, `_check_scope`, `scope=` param on `validate_job_fields`. |
| `src/cc_session_tools/lib/scheduler/store.py` | `scope` column in `_DDL` + in `_migrate_jobs_table`. |
| `src/cc_session_tools/lib/scheduler/registry.py` | Read/write `scope` in `_spec_from_row`, `add_job`, `replace_job`, `load_registry`'s SELECT. |
| `src/cc_session_tools/lib/scheduler/bundled_jobs.py` | Add `BundledJob.scope`. |
| `src/cc_session_tools/cli/ccsched.py` | `--scope` on `add` and `edit`; `scope` row in `show`. |
| `src/cc_session_tools/cli/ccst.py` | `_cmd_ccsched_jobs_export`, `_cmd_ccsched_jobs_sync`, their parsers, and dispatch. |
| `tests/scheduler/test_jobspec.py` | Scope validation tests. |
| `tests/scheduler/test_store.py` | Column-migration test. |
| `tests/scheduler/test_registry.py` | Round-trip test. |
| `tests/test_scheduler_bundled_jobs.py` | Default-scope test. |
| `tests/scheduler/test_ccsched_cli.py` | `--scope` CLI tests. |
| `tests/test_ccst_ccsched_jobs_cli.py` | `export` / `sync` CLI tests. |

---

## Conventions to follow (read before starting)

- **Tests run via** `uv run pytest -q` from the worktree root. Single test:
  `uv run pytest tests/scheduler/test_jobspec.py::test_name -q`.
- **Existing test isolation pattern:** scheduler unit tests use
  `monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))`. CLI tests shell out via
  `subprocess.run([sys.executable, "-m", "cc_session_tools.cli.<mod>", ...], env=env)` with
  `CC_SCHEDULER_DIR` (ccsched) or `CCST_DATA_HOME` (ccst) pointed at `tmp_path`. Match whichever
  the file you are editing already uses.
- **No personal identifiers anywhere.** Use `/home/alice`, `/example/repos/...` style fictional
  paths in tests and docstrings. Never a real home directory path.
- **Error convention in `ccst.py`:** `print(f"error: {exc}", file=sys.stderr)` then `return 2`.
  **In `ccsched.py`:** the existing `_err(msg)` helper (prints `ccsched: <msg>` to stderr,
  returns 2).
- **Commit style:** imperative mood, conventional prefix, explain WHY. One logical change per
  commit. Never commit with a failing suite.
- **Type hints on every signature**, `from __future__ import annotations` at the top of every
  module (every existing scheduler module does this).

---

## Task 1: `JobScope` enum and `JobSpec.scope`

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/jobspec.py`
- Test: `tests/scheduler/test_jobspec.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/scheduler/test_jobspec.py`:

```python
def test_scope_defaults_to_local() -> None:
    spec = validate_job_fields(
        job_id="j", cadence="daily@09:00", coalesce="one", command=["true"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
    )
    assert spec.scope is JobScope.LOCAL


def test_scope_accepts_explicit_local() -> None:
    spec = validate_job_fields(
        job_id="j", cadence="daily@09:00", coalesce="one", command=["true"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
        scope="local",
    )
    assert spec.scope is JobScope.LOCAL


def test_scope_accepts_cross_machine() -> None:
    spec = validate_job_fields(
        job_id="j", cadence="daily@09:00", coalesce="one", command=["true"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
        scope="cross-machine",
    )
    assert spec.scope is JobScope.CROSS_MACHINE


def test_scope_rejects_unknown_value_naming_the_valid_ones() -> None:
    with pytest.raises(JobValidationError) as exc:
        validate_job_fields(
            job_id="j", cadence="daily@09:00", coalesce="one", command=["true"],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
            scope="global",
        )
    assert "global" in str(exc.value)
    assert "cross-machine" in str(exc.value)
```

Add `JobScope` to that file's existing import from `cc_session_tools.lib.scheduler.jobspec`.
Check the file's current import line and extend it — do not add a second import statement.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_jobspec.py -q`
Expected: FAIL — `ImportError: cannot import name 'JobScope'`.

- [ ] **Step 3: Implement**

In `src/cc_session_tools/lib/scheduler/jobspec.py`, after the `CoalesceKind` class:

```python
class JobScope(str, Enum):
    """Whether a job is meant for this machine only, or is one definition shared
    across every machine via the claude-code-config-sync manifest. Only affects
    `ccst ccsched-jobs export`/`sync`; execution is identical either way."""

    LOCAL = "local"
    CROSS_MACHINE = "cross-machine"
```

Add the field to `JobSpec`, after `success_exit_codes` (it must come after, because both have
defaults and `success_exit_codes` is already last):

```python
    scope: JobScope = JobScope.LOCAL
```

Add the checker next to `_check_coalesce`:

```python
def _check_scope(scope: str) -> JobScope:
    try:
        return JobScope(scope)
    except ValueError as exc:
        raise JobValidationError(
            f"invalid scope {scope!r}: must be 'local' or 'cross-machine'"
        ) from exc
```

Extend `validate_job_fields`: add the keyword-only parameter
`scope: str = JobScope.LOCAL.value,` after `success_exit_codes`, call
`scope_kind = _check_scope(scope)` after `_check_success_exit_codes(...)`, and pass
`scope=scope_kind,` in the returned `JobSpec(...)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_jobspec.py -q`
Expected: PASS (all tests in the file, old and new).

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/jobspec.py tests/scheduler/test_jobspec.py
git commit -m "feat(scheduler): let a job declare whether it is local or cross-machine

Jobs have until now been implicitly local to the machine they were registered
on, with no way to say a definition is meant to run identically everywhere.
Default to local so every existing job keeps its current behaviour untouched."
```

---

## Task 2: persist `scope` in `ccsched.db`

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/store.py`
- Modify: `src/cc_session_tools/lib/scheduler/registry.py`
- Test: `tests/scheduler/test_store.py`, `tests/scheduler/test_registry.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/scheduler/test_store.py` (mirroring the existing
`test_success_exit_codes_column_backfilled_on_pre_existing_db`):

```python
def test_scope_column_backfilled_on_pre_existing_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A jobs table created before scope existed must gain the column with its
    'local' default on the next connect(), so pre-existing rows keep the
    behaviour they already had rather than erroring or reading as NULL."""
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    conn = store.connect()
    conn.execute(
        "INSERT INTO jobs (job_id, cadence, coalesce_kind, command, surface, "
        "enabled, catchup_window, timeout) VALUES "
        "('legacy', 'daily@09:00', 'one', '[\"true\"]', 1, 1, '7d', '60s')"
    )
    conn.execute("ALTER TABLE jobs DROP COLUMN scope")
    conn.commit()
    conn.close()

    conn = store.connect()
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        assert "scope" in cols
        row = conn.execute("SELECT scope FROM jobs WHERE job_id='legacy'").fetchone()
        assert row["scope"] == "local"
    finally:
        conn.close()


def test_scope_column_rejects_an_unknown_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    conn = store.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO jobs (job_id, cadence, coalesce_kind, command, surface, "
                "enabled, catchup_window, timeout, scope) VALUES "
                "('j', 'daily@09:00', 'one', '[\"true\"]', 1, 1, '7d', '60s', 'global')"
            )
    finally:
        conn.close()
```

`tests/scheduler/test_store.py` needs `import sqlite3` added at the top if it is not already
imported.

Append to `tests/scheduler/test_registry.py`:

```python
def test_scope_round_trips_through_add_and_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    spec = validate_job_fields(
        job_id="shared-job", cadence="daily@09:00", coalesce="one", command=["true"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
        scope="cross-machine",
    )
    reg.add_job(spec)
    assert reg.load_registry()[0].scope is JobScope.CROSS_MACHINE

    reg.replace_job(
        validate_job_fields(
            job_id="shared-job", cadence="daily@09:00", coalesce="one", command=["true"],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
            scope="local",
        )
    )
    assert reg.load_registry()[0].scope is JobScope.LOCAL


def test_scope_defaults_to_local_when_added_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    reg.add_job(_spec())
    assert reg.load_registry()[0].scope is JobScope.LOCAL
```

Extend that file's existing `from cc_session_tools.lib.scheduler.jobspec import ...` line with
`JobScope`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_store.py tests/scheduler/test_registry.py -q`
Expected: FAIL — no such column `scope` / `ImportError` for `JobScope`.

- [ ] **Step 3: Implement**

In `store.py`'s `_DDL`, add to the `jobs` table after `success_exit_codes`:

```
    scope              TEXT NOT NULL DEFAULT 'local' CHECK (scope IN ('local', 'cross-machine'))
```

(Remember to add the trailing comma to the `success_exit_codes` line above it.)

In `_migrate_jobs_table`, after the `success_exit_codes` block:

```python
    if "scope" not in cols:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN scope TEXT NOT NULL DEFAULT 'local' "
            "CHECK (scope IN ('local', 'cross-machine'))"
        )
        conn.commit()
```

SQLite does allow a `CHECK` constraint on `ALTER TABLE ... ADD COLUMN` (verified), so migrated
DBs and freshly created DBs end up with identical constraints.

In `registry.py`:
- extend the import to `from ...jobspec import CoalesceKind, JobScope, JobSpec`
- `_spec_from_row`: add `scope=JobScope(row["scope"]),`
- `load_registry`'s SELECT: add `, scope` after `success_exit_codes`
- `add_job`: add `scope` to the column list, one more `?`, and `spec.scope.value` to the values
  tuple
- `replace_job`: add `scope=?` to the SET clause and `spec.scope.value` to the values tuple, both
  **before** the trailing `spec.job_id` that binds the WHERE clause

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler -q`
Expected: PASS — the whole scheduler suite, not just the two files.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/store.py \
        src/cc_session_tools/lib/scheduler/registry.py \
        tests/scheduler/test_store.py tests/scheduler/test_registry.py
git commit -m "feat(scheduler): persist job scope in ccsched.db

Adds the column through the existing idempotent migration hook so an on-disk
jobs table predating this change gains it with the 'local' default rather than
needing a migration step, keeping this an additive, non-breaking change."
```

---

## Task 3: `BundledJob.scope`

**Files:**
- Modify: `src/cc_session_tools/lib/scheduler/bundled_jobs.py`
- Modify: `src/cc_session_tools/cli/ccst.py` (`_cmd_ccsched_jobs_install`, ~line 1362)
- Test: `tests/test_scheduler_bundled_jobs.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scheduler_bundled_jobs.py`:

```python
def test_every_bundled_job_is_local_scope():
    """Bundled jobs ship inside the package itself and are provisioned by
    `ccst ccsched-jobs install`; they never travel through the CCCS manifest, so
    none of them should be declaring itself cross-machine."""
    for job in bundled_jobs.BUNDLED_CCSCHED_JOBS:
        assert job.scope is JobScope.LOCAL, job.job_id
```

Add `from cc_session_tools.lib.scheduler.jobspec import JobScope` to that file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scheduler_bundled_jobs.py -q`
Expected: FAIL — `AttributeError: 'BundledJob' object has no attribute 'scope'`.

- [ ] **Step 3: Implement**

In `bundled_jobs.py`, import `JobScope` and add the field to `BundledJob` after
`success_exit_codes`:

```python
from cc_session_tools.lib.scheduler.jobspec import JobScope
```

```python
    scope: JobScope = JobScope.LOCAL
```

Update the `BundledJob` module docstring's closing line to mention that `scope` exists so the
dataclass matches `JobSpec`'s shape, and that bundled entries stay `LOCAL`.

In `ccst.py`'s `_cmd_ccsched_jobs_install`, pass the field through to `validate_job_fields` so
there is no scope-shaped special case (spec §1):

```python
            success_exit_codes=job.success_exit_codes,
            scope=job.scope.value,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scheduler_bundled_jobs.py tests/test_ccst_ccsched_jobs_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/bundled_jobs.py \
        src/cc_session_tools/cli/ccst.py tests/test_scheduler_bundled_jobs.py
git commit -m "feat(scheduler): give BundledJob the same scope field as JobSpec

Keeps the two record shapes identical so the installer can hand a BundledJob
straight to validate_job_fields without a scope-shaped special case."
```

---

## Task 4: `ccsched add/edit --scope` and `ccsched show`

**Files:**
- Modify: `src/cc_session_tools/cli/ccsched.py`
- Test: `tests/scheduler/test_ccsched_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/scheduler/test_ccsched_cli.py`:

```python
def test_add_defaults_to_local_scope(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    _add_ok(tmp_path)
    res = _run(["show", "tesco"], sched, hooks)
    assert res.returncode == 0, res.stderr
    assert "scope:" in res.stdout
    assert "local" in res.stdout


def test_add_accepts_cross_machine_scope(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(
        ["add", "--id", "shared", "--cadence", "daily@09:00", "--timeout", "5s",
         "--scope", "cross-machine", "--command", "true"],
        sched, hooks,
    )
    assert res.returncode == 0, res.stderr
    show = _run(["show", "shared"], sched, hooks)
    assert "cross-machine" in show.stdout


def test_add_rejects_an_unknown_scope(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(
        ["add", "--id", "j", "--cadence", "daily@09:00", "--scope", "global",
         "--command", "true"],
        sched, hooks,
    )
    assert res.returncode == 2
    assert "scope" in (res.stderr + res.stdout).lower()


def test_edit_changes_scope(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    _add_ok(tmp_path)
    res = _run(["edit", "tesco", "--scope", "cross-machine"], sched, hooks)
    assert res.returncode == 0, res.stderr
    assert "cross-machine" in _run(["show", "tesco"], sched, hooks).stdout


def test_edit_preserves_scope_when_not_passed(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    _run(
        ["add", "--id", "shared", "--cadence", "daily@09:00", "--timeout", "5s",
         "--scope", "cross-machine", "--command", "true"],
        sched, hooks,
    )
    _run(["edit", "shared", "--timeout", "9s"], sched, hooks)
    assert "cross-machine" in _run(["show", "shared"], sched, hooks).stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_ccsched_cli.py -q`
Expected: FAIL — `unrecognized arguments: --scope`.

- [ ] **Step 3: Implement**

Note `--command` uses `nargs=argparse.REMAINDER`, so `--scope` must be declared before it in the
parser and passed before `--command` on the command line — the tests above already do that.

In `_build_parser`, add to `add_p` (immediately after the `--success-exit-codes` block, before
the surface group):

```python
    add_p.add_argument(
        "--scope", default="local", choices=("local", "cross-machine"),
        help=(
            "'local' (default) means this job is meant for this machine only. "
            "'cross-machine' marks it as one shared definition that travels "
            "between machines via the claude-code-config-sync manifest - see "
            "'ccst ccsched-jobs export' and 'ccst ccsched-jobs sync'."
        ),
    )
```

And to `edit_p`, after `--success-exit-codes`:

```python
    edit_p.add_argument("--scope", default=None, choices=("local", "cross-machine"))
```

In `_cmd_add`, add `scope=args.scope,` to the `validate_job_fields(...)` call.

In `_cmd_edit`, add `scope=(args.scope or cur.scope.value),` to the `validate_job_fields(...)`
call.

In `_cmd_show`'s `fields` list, insert after the `("coalesce", ...)` entry:

```python
        ("scope", spec.scope.value),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccsched.py tests/scheduler/test_ccsched_cli.py
git commit -m "feat(ccsched): expose job scope on add, edit and show

argparse choices= rejects an unknown scope at the CLI boundary, so the value
reaching validate_job_fields is already one of the two the enum knows about."
```

---

## Task 5: machine-values file and `{{PLACEHOLDER}}` resolution (spec §3)

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/machine_values.py`
- Test: `tests/scheduler/test_machine_values.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/scheduler/test_machine_values.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import machine_values as mv


def test_default_path_is_under_the_claude_home_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(mv.MACHINE_VALUES_ENV, raising=False)
    assert mv.machine_values_path() == Path.home() / ".claude" / "ccsched-machine-values.json"


def test_path_is_overridable_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(mv.MACHINE_VALUES_ENV, str(tmp_path / "vals.json"))
    assert mv.machine_values_path() == tmp_path / "vals.json"


def test_missing_file_loads_as_empty(tmp_path: Path) -> None:
    assert mv.load_machine_values(tmp_path / "absent.json") == {}


def test_loads_a_flat_string_map(tmp_path: Path) -> None:
    p = tmp_path / "vals.json"
    p.write_text(json.dumps({"HOME_DIR": "/home/alice", "MOUNT": "/mnt/data"}))
    assert mv.load_machine_values(p) == {"HOME_DIR": "/home/alice", "MOUNT": "/mnt/data"}


def test_rejects_a_non_object_top_level(tmp_path: Path) -> None:
    p = tmp_path / "vals.json"
    p.write_text(json.dumps(["/home/alice"]))
    with pytest.raises(mv.MachineValuesError) as exc:
        mv.load_machine_values(p)
    assert str(p) in str(exc.value)


def test_rejects_a_non_string_value(tmp_path: Path) -> None:
    p = tmp_path / "vals.json"
    p.write_text(json.dumps({"PORT": 8080}))
    with pytest.raises(mv.MachineValuesError) as exc:
        mv.load_machine_values(p)
    assert "PORT" in str(exc.value)


def test_rejects_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "vals.json"
    p.write_text("{not json")
    with pytest.raises(mv.MachineValuesError) as exc:
        mv.load_machine_values(p)
    assert str(p) in str(exc.value)


def test_command_without_placeholders_is_returned_unchanged() -> None:
    cmd = ("ccst", "pdata", "verify", "--all-projects")
    assert mv.resolve_placeholders(cmd, {}) == cmd


def test_resolves_every_placeholder() -> None:
    out = mv.resolve_placeholders(
        ("bash", "{{HOME_DIR}}/scripts/foo.sh", "--out={{MOUNT}}/x"),
        {"HOME_DIR": "/home/alice", "MOUNT": "/mnt/data"},
    )
    assert out == ("bash", "/home/alice/scripts/foo.sh", "--out=/mnt/data/x")


def test_resolves_two_placeholders_in_one_argv_element() -> None:
    out = mv.resolve_placeholders(("{{A}}/{{B}}",), {"A": "/one", "B": "two"})
    assert out == ("/one/two",)


def test_missing_key_raises_naming_that_key() -> None:
    with pytest.raises(mv.PlaceholderError) as exc:
        mv.resolve_placeholders(("bash", "{{MOUNT}}/x"), {"HOME_DIR": "/home/alice"})
    assert "MOUNT" in str(exc.value)


def test_missing_key_error_names_every_missing_key() -> None:
    with pytest.raises(mv.PlaceholderError) as exc:
        mv.resolve_placeholders(("{{A}}", "{{B}}"), {})
    assert "A" in str(exc.value)
    assert "B" in str(exc.value)


def test_find_placeholders_reports_the_token_names() -> None:
    assert mv.find_placeholders(("{{A}}/x", "plain", "{{B}}")) == ("A", "B")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_machine_values.py -q`
Expected: FAIL — `ModuleNotFoundError: cc_session_tools.lib.scheduler.machine_values`.

- [ ] **Step 3: Implement**

Create `src/cc_session_tools/lib/scheduler/machine_values.py`:

```python
"""Machine-local values for {{PLACEHOLDER}} tokens in a cross-machine job's argv.

A cross-machine job definition is shared verbatim between machines through the
claude-code-config-sync manifest, so any part of its command that differs per
machine (a mount point, a clone location) travels as a {{TOKEN}} and is resolved
here at sync time from a file that is deliberately NOT git-tracked.

An unresolved token is a hard error naming the key, never a silent skip or an
invented default: a scheduled job runs unattended, so a command assembled from a
guessed value is strictly worse than one that refuses to be registered at all.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

MACHINE_VALUES_ENV = "CCSCHED_MACHINE_VALUES"

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


class MachineValuesError(ValueError):
    """Raised when the machine-values file exists but is not a flat string map."""


class PlaceholderError(ValueError):
    """Raised when a command holds a {{TOKEN}} with no matching key locally."""


def machine_values_path() -> Path:
    """Location of the machine-local values file. Overridable via
    CCSCHED_MACHINE_VALUES (tests, non-standard setups)."""
    override = os.environ.get(MACHINE_VALUES_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude" / "ccsched-machine-values.json"


def load_machine_values(path: Path) -> dict[str, str]:
    """Read the values file. An absent file is an empty map, not an error - a
    cross-machine job with no placeholders must sync on a machine that has never
    needed one. Only an actually-unresolvable token fails, in resolve_placeholders.
    """
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise MachineValuesError(f"cannot read machine values file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MachineValuesError(
            f"machine values file {path} must hold a JSON object mapping "
            f"placeholder names to strings, not {type(raw).__name__}"
        )
    for key, value in raw.items():
        if not isinstance(value, str):
            raise MachineValuesError(
                f"machine values file {path}: key {key!r} must map to a string, "
                f"not {type(value).__name__}"
            )
    return dict(raw)


def find_placeholders(command: tuple[str, ...]) -> tuple[str, ...]:
    """Every distinct {{TOKEN}} name in an argv, in first-seen order."""
    seen: list[str] = []
    for part in command:
        for name in _PLACEHOLDER_RE.findall(part):
            if name not in seen:
                seen.append(name)
    return tuple(seen)


def resolve_placeholders(
    command: tuple[str, ...], values: Mapping[str, str]
) -> tuple[str, ...]:
    """Substitute every {{TOKEN}} in argv from values, or raise naming what is
    missing. Pure: the caller supplies the values and adds file-path context to
    the error message."""
    missing = [name for name in find_placeholders(command) if name not in values]
    if missing:
        raise PlaceholderError(
            "unresolved placeholder(s) " + ", ".join(f"{{{{{n}}}}}" for n in missing)
            + " in job command: add " + ", ".join(repr(n) for n in missing)
            + " to the machine values file"
        )
    return tuple(
        _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], part) for part in command
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_machine_values.py -q`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/machine_values.py \
        tests/scheduler/test_machine_values.py
git commit -m "feat(scheduler): resolve {{PLACEHOLDER}} tokens from a machine-local values file

A job definition shared between machines cannot hard-code a path that only
exists on one of them. Resolving at sync time from a non-git-tracked file keeps
the shared definition portable, and refusing an unresolved token keeps an
unattended job from ever running a half-substituted command."
```

---

## Task 6: the CCCS manifest shape (serialise + parse)

**Files:**
- Create: `src/cc_session_tools/lib/scheduler/manifest.py`
- Test: `tests/scheduler/test_manifest.py`

This is the single source of truth for the manifest's JSON shape. `export` (Task 7) and `sync`
(Task 8) both go through it, so the writer and the reader can never drift.

- [ ] **Step 1: Write the failing tests**

Create `tests/scheduler/test_manifest.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import manifest
from cc_session_tools.lib.scheduler.jobspec import JobScope, validate_job_fields


def _spec(job_id: str = "shared-job", command: list[str] | None = None):
    return validate_job_fields(
        job_id=job_id, cadence="daily@09:00", coalesce="one",
        command=command if command is not None else ["ccst", "doctor"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
        scope="cross-machine",
    )


def _entry(**overrides) -> dict:
    entry = {
        "job_id": "shared-job",
        "cadence": "daily@09:00",
        "coalesce": "one",
        "catchup_window": "7d",
        "timeout": "60s",
        "surface": True,
        "command": ["ccst", "doctor"],
        "success_exit_codes": [0],
        "scope": "cross-machine",
    }
    entry.update(overrides)
    return entry


def test_serialises_every_manifest_field() -> None:
    assert manifest.job_to_manifest_entry(_spec()) == _entry()


def test_serialised_entry_omits_local_only_enabled_state() -> None:
    """enabled is per-machine operational state managed by ccsched enable/disable;
    the manifest must not carry it or a locally-disabled job would silently
    re-enable on the next sync."""
    assert "enabled" not in manifest.job_to_manifest_entry(_spec())


def test_export_output_round_trips_through_the_parser(tmp_path: Path) -> None:
    path = tmp_path / "ccsched-jobs.json"
    path.write_text(json.dumps([manifest.job_to_manifest_entry(_spec())]))
    parsed = manifest.parse_manifest(path, values={})
    assert len(parsed) == 1
    assert parsed[0].job_id == "shared-job"
    assert parsed[0].scope is JobScope.CROSS_MACHINE
    assert parsed[0].command == ("ccst", "doctor")


def test_missing_manifest_file_is_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "absent.json"
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.parse_manifest(path, values={})
    assert str(path) in str(exc.value)


def test_manifest_must_be_a_json_array(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps(_entry()))
    with pytest.raises(manifest.ManifestError):
        manifest.parse_manifest(path, values={})


def test_malformed_json_is_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text("[{,]")
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.parse_manifest(path, values={})
    assert str(path) in str(exc.value)


def test_entry_missing_a_required_key_is_rejected_naming_it(tmp_path: Path) -> None:
    entry = _entry()
    del entry["cadence"]
    path = tmp_path / "m.json"
    path.write_text(json.dumps([entry]))
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.parse_manifest(path, values={})
    assert "cadence" in str(exc.value)


def test_entry_with_an_unknown_key_is_rejected_naming_it(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([_entry(retries=3)]))
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.parse_manifest(path, values={})
    assert "retries" in str(exc.value)


def test_local_scope_entry_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([_entry(scope="local")]))
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.parse_manifest(path, values={})
    assert "cross-machine" in str(exc.value)


def test_entry_without_an_explicit_scope_is_rejected(tmp_path: Path) -> None:
    entry = _entry()
    del entry["scope"]
    path = tmp_path / "m.json"
    path.write_text(json.dumps([entry]))
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.parse_manifest(path, values={})
    assert "scope" in str(exc.value)


def test_invalid_job_field_is_rejected_naming_the_job(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([_entry(cadence="hourly")]))
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.parse_manifest(path, values={})
    assert "shared-job" in str(exc.value)


def test_duplicate_job_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([_entry(), _entry()]))
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.parse_manifest(path, values={})
    assert "shared-job" in str(exc.value)


def test_placeholders_are_resolved_from_the_supplied_values(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([_entry(command=["bash", "{{HOME_DIR}}/x.sh"])]))
    parsed = manifest.parse_manifest(path, values={"HOME_DIR": "/home/alice"})
    assert parsed[0].command == ("bash", "/home/alice/x.sh")


def test_unresolved_placeholder_is_rejected_naming_the_key_and_the_job(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps([_entry(command=["bash", "{{HOME_DIR}}/x.sh"])]))
    with pytest.raises(manifest.ManifestError) as exc:
        manifest.parse_manifest(path, values={})
    assert "HOME_DIR" in str(exc.value)
    assert "shared-job" in str(exc.value)


def test_default_manifest_path_points_at_the_cccs_clone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert manifest.default_manifest_path() == (
        tmp_path / "repos" / "claude-code-config-sync" / "config" / "ccsched-jobs.json"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_manifest.py -q`
Expected: FAIL — `ModuleNotFoundError: cc_session_tools.lib.scheduler.manifest`.

- [ ] **Step 3: Implement**

Create `src/cc_session_tools/lib/scheduler/manifest.py`:

```python
"""The claude-code-config-sync (CCCS) ccsched-jobs manifest: one JSON array of
cross-machine job definitions, shaped like BundledJob.

Both `ccst ccsched-jobs export` (which writes an entry) and
`ccst ccsched-jobs sync` (which reads them) go through this module, so the
serialiser and the parser cannot drift apart.

`enabled` is deliberately absent from the shape. It is per-machine operational
state owned by `ccsched enable`/`disable`; carrying it in a shared file would let
one machine's pause silently propagate to every other machine.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cc_session_tools.lib.scheduler.jobspec import (
    JobScope,
    JobSpec,
    JobValidationError,
    validate_job_fields,
)
from cc_session_tools.lib.scheduler.machine_values import (
    PlaceholderError,
    resolve_placeholders,
)

MANIFEST_RELATIVE_PATH = Path("repos") / "claude-code-config-sync" / "config" / "ccsched-jobs.json"

_REQUIRED_KEYS = frozenset({
    "job_id", "cadence", "coalesce", "catchup_window", "timeout",
    "surface", "command", "success_exit_codes", "scope",
})


class ManifestError(ValueError):
    """Raised for an unreadable, malformed, or invalid manifest."""


def default_manifest_path() -> Path:
    """Where the CCCS clone normally lives - the same location
    check-config-drift.sh hardcodes as its REPO_DIR. Override on the CLI with
    --manifest for a non-default clone."""
    return Path.home() / MANIFEST_RELATIVE_PATH


def job_to_manifest_entry(spec: JobSpec) -> dict[str, Any]:
    """Serialise one JobSpec into the manifest's entry shape."""
    return {
        "job_id": spec.job_id,
        "cadence": spec.cadence,
        "coalesce": spec.coalesce.value,
        "catchup_window": spec.catchup_window,
        "timeout": spec.timeout,
        "surface": spec.surface,
        "command": list(spec.command),
        "success_exit_codes": list(spec.success_exit_codes),
        "scope": spec.scope.value,
    }


def _entry_to_spec(entry: Any, index: int, values: Mapping[str, str]) -> JobSpec:
    if not isinstance(entry, dict):
        raise ManifestError(f"entry {index} must be a JSON object, not {type(entry).__name__}")
    keys = set(entry)
    missing = _REQUIRED_KEYS - keys
    if missing:
        raise ManifestError(
            f"entry {index} is missing required key(s): {', '.join(sorted(missing))}"
        )
    unknown = keys - _REQUIRED_KEYS
    if unknown:
        raise ManifestError(
            f"entry {index} has unknown key(s): {', '.join(sorted(unknown))}"
        )

    job_id = entry["job_id"]
    if entry["scope"] != JobScope.CROSS_MACHINE.value:
        raise ManifestError(
            f"job {job_id!r}: manifest entries must declare "
            f"\"scope\": \"{JobScope.CROSS_MACHINE.value}\" - the manifest holds only "
            f"cross-machine jobs, got {entry['scope']!r}"
        )

    command = entry["command"]
    if not isinstance(command, list) or not all(isinstance(p, str) for p in command):
        raise ManifestError(f"job {job_id!r}: command must be a list of strings")
    try:
        resolved = resolve_placeholders(tuple(command), values)
    except PlaceholderError as exc:
        raise ManifestError(f"job {job_id!r}: {exc}") from exc

    codes = entry["success_exit_codes"]
    if not isinstance(codes, list) or not all(isinstance(c, int) for c in codes):
        raise ManifestError(f"job {job_id!r}: success_exit_codes must be a list of integers")

    try:
        return validate_job_fields(
            job_id=job_id,
            cadence=entry["cadence"],
            coalesce=entry["coalesce"],
            command=list(resolved),
            surface=bool(entry["surface"]),
            enabled=True,
            catchup_window=entry["catchup_window"],
            timeout=entry["timeout"],
            success_exit_codes=tuple(codes),
            scope=entry["scope"],
        )
    except JobValidationError as exc:
        raise ManifestError(f"job {job_id!r}: {exc}") from exc


def parse_manifest(path: Path, values: Mapping[str, str]) -> list[JobSpec]:
    """Read, validate and templatise the manifest at path.

    `enabled=True` on every returned spec is a placeholder the caller replaces
    with the local value when updating an existing job (see ccst.py's sync
    command) - the manifest itself never expresses enabled state.
    """
    if not path.is_file():
        raise ManifestError(
            f"no ccsched-jobs manifest at {path} - check the claude-code-config-sync "
            "clone location, or pass --manifest PATH"
        )
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise ManifestError(
            f"manifest {path} must hold a JSON array of job objects, not {type(raw).__name__}"
        )

    specs = [_entry_to_spec(entry, i, values) for i, entry in enumerate(raw)]
    seen: set[str] = set()
    for spec in specs:
        if spec.job_id in seen:
            raise ManifestError(f"manifest {path} lists job {spec.job_id!r} more than once")
        seen.add(spec.job_id)
    return specs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_manifest.py -q`
Expected: PASS (16 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/scheduler/manifest.py tests/scheduler/test_manifest.py
git commit -m "feat(scheduler): add one shared implementation of the CCCS jobs manifest shape

Export writes this shape and sync reads it; giving each its own serialiser
would guarantee they drift. Rejecting unknown and missing keys outright means a
hand-edited manifest fails loudly rather than half-applying."
```

---

## Task 7: `ccst ccsched-jobs export <job-id>` (spec §4)

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`
- Test: `tests/test_ccst_ccsched_jobs_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ccst_ccsched_jobs_cli.py` (this file's `_run` helper already shells out to
`ccst`; add a small `_ccsched` helper alongside it if one is not already present):

```python
import json


def _ccsched(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent),
        env=env,
    )


def _add_cross_machine_job(env: dict, job_id: str = "shared-job", command: str = "true") -> None:
    r = _ccsched(
        env, "add", "--id", job_id, "--cadence", "daily@09:00", "--timeout", "60s",
        "--catchup-window", "7d", "--scope", "cross-machine", "--command", command,
    )
    assert r.returncode == 0, r.stderr


def test_export_prints_the_manifest_entry_for_a_cross_machine_job(base_env):
    _add_cross_machine_job(base_env)
    r = _run(base_env, "ccsched-jobs", "export", "shared-job")
    assert r.returncode == 0, r.stderr
    entry = json.loads(r.stdout)
    assert entry["job_id"] == "shared-job"
    assert entry["scope"] == "cross-machine"
    assert entry["command"] == ["true"]
    assert entry["cadence"] == "daily@09:00"
    assert "enabled" not in entry


def test_export_of_an_unknown_job_id_errors(base_env):
    r = _run(base_env, "ccsched-jobs", "export", "nope")
    assert r.returncode == 2
    assert "nope" in r.stderr


def test_export_refuses_a_local_scope_job(base_env):
    r = _ccsched(
        base_env, "add", "--id", "local-job", "--cadence", "daily@09:00",
        "--timeout", "60s", "--command", "true",
    )
    assert r.returncode == 0, r.stderr
    r = _run(base_env, "ccsched-jobs", "export", "local-job")
    assert r.returncode == 2
    assert "cross-machine" in r.stderr


def test_export_does_not_reverse_templating_of_local_values(base_env):
    """Spec section 4: export is a serialiser, not a de-templatiser. A literal
    machine-specific path in the command comes out verbatim for the operator to
    replace with a {{PLACEHOLDER}} by hand before committing."""
    _add_cross_machine_job(base_env, job_id="pathy", command="/home/alice/scripts/x.sh")
    r = _run(base_env, "ccsched-jobs", "export", "pathy")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["command"] == ["/home/alice/scripts/x.sh"]


def test_export_output_is_accepted_by_sync(base_env, tmp_path):
    _add_cross_machine_job(base_env)
    exported = _run(base_env, "ccsched-jobs", "export", "shared-job")
    manifest = tmp_path / "ccsched-jobs.json"
    manifest.write_text(json.dumps([json.loads(exported.stdout)]))
    r = _run(base_env, "ccsched-jobs", "sync", "--manifest", str(manifest))
    assert r.returncode == 0, r.stderr
    assert "unchanged: shared-job" in r.stdout
```

The last test also covers Task 8; it will keep failing until that task lands. Note that in this
file `base_env` sets `CCST_DATA_HOME`, which `paths.data_home()` (and therefore
`store.scheduler_dir()`) honours, so both CLIs share one `ccsched.db` under `tmp_path`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_ccsched_jobs_cli.py -q`
Expected: FAIL — `invalid choice: 'export'`.

- [ ] **Step 3: Implement**

In `ccst.py`, after `_cmd_ccsched_jobs_install`:

```python
def _cmd_ccsched_jobs_export(args: argparse.Namespace) -> int:
    """Serialise one local cross-machine job into the CCCS manifest's entry shape and
    print it to stdout, for the operator to paste into
    claude-code-config-sync/config/ccsched-jobs.json and commit by hand.

    Deliberately does not reverse the {{PLACEHOLDER}} templating (spec section 4): only
    the operator knows which literal value in a command is a machine-specific one meant
    to travel as a placeholder and which is meant to stay literal, so guessing would
    silently produce a job that runs the wrong command on the other machine."""
    from cc_session_tools.lib.scheduler import manifest, registry
    from cc_session_tools.lib.scheduler.jobspec import JobScope

    job_id: str = args.job_id
    spec = next((s for s in registry.load_registry() if s.job_id == job_id), None)
    if spec is None:
        print(f"error: unknown job id: {job_id!r}", file=sys.stderr)
        return 2
    if spec.scope is not JobScope.CROSS_MACHINE:
        print(
            f"error: job {job_id!r} has scope {spec.scope.value!r}; the manifest holds "
            f"only cross-machine jobs. Run "
            f"'ccsched edit {job_id} --scope cross-machine' first if it should travel.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(manifest.job_to_manifest_entry(spec), indent=2))
    return 0
```

Confirm `json` and `sys` are already imported at the top of `ccst.py` (they are); do not add
duplicate imports.

Register the subparser next to the existing `install` one (~line 2043):

```python
    ccsched_jobs_export_parser = ccsched_jobs_sub.add_parser(
        "export",
        help="Print one cross-machine job as a CCCS manifest entry (JSON, to stdout)",
    )
    ccsched_jobs_export_parser.add_argument(
        "job_id", metavar="<job-id>", help="Id of the cross-machine job to serialise",
    )
```

Extend the dispatch at ~line 2191:

```python
    if args.noun == "ccsched-jobs":
        if args.verb == "install":
            sys.exit(_cmd_ccsched_jobs_install(args))
        if args.verb == "export":
            sys.exit(_cmd_ccsched_jobs_export(args))
```

Read the existing dispatch block first and match its exact structure — it may use `if`/`elif`.

Also extend the `ccsched-jobs install` line in the module-level usage/help text near the top of
`ccst.py` (~line 56) so the new verbs are discoverable there too.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_ccsched_jobs_cli.py -q`
Expected: all PASS except `test_export_output_is_accepted_by_sync`, which fails until Task 8.
Confirm the failure is exactly that one test and its message is about `sync`.

- [ ] **Step 5: Commit**

Commit only once the suite is green. Since one test intentionally depends on Task 8, mark it
`@pytest.mark.xfail(reason="sync lands in the next commit", strict=True)` for this commit and
remove the marker in Task 8, so no commit ever lands with a failing suite.

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_ccsched_jobs_cli.py
git commit -m "feat(ccst): add ccsched-jobs export for the local-to-CCCS direction

Moving a job definition to the shared manifest by hand means retyping nine
fields in the right JSON shape. Printing it instead removes that transcription
step without introducing any automated git write."
```

---

## Task 8: `ccst ccsched-jobs sync [--apply]` (spec §6)

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`
- Test: `tests/test_ccst_ccsched_jobs_cli.py`

- [ ] **Step 1: Write the failing tests**

Remove the `xfail` marker added in Task 7, and append:

```python
def _write_manifest(tmp_path, *entries) -> Path:
    path = tmp_path / "ccsched-jobs.json"
    path.write_text(json.dumps(list(entries)))
    return path


def _manifest_entry(**overrides) -> dict:
    entry = {
        "job_id": "shared-job",
        "cadence": "daily@09:00",
        "coalesce": "one",
        "catchup_window": "7d",
        "timeout": "60s",
        "surface": True,
        "command": ["true"],
        "success_exit_codes": [0],
        "scope": "cross-machine",
    }
    entry.update(overrides)
    return entry


def _show(env: dict, job_id: str) -> str:
    r = _ccsched(env, "show", job_id)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_sync_dry_run_reports_a_missing_job_without_adding_it(base_env, tmp_path):
    path = _write_manifest(tmp_path, _manifest_entry())
    r = _run(base_env, "ccsched-jobs", "sync", "--manifest", str(path))
    assert r.returncode == 0, r.stderr
    assert "would add: shared-job" in r.stdout
    assert _ccsched(base_env, "show", "shared-job").returncode == 2


def test_sync_apply_adds_a_missing_job(base_env, tmp_path):
    path = _write_manifest(tmp_path, _manifest_entry())
    r = _run(base_env, "ccsched-jobs", "sync", "--manifest", str(path), "--apply")
    assert r.returncode == 0, r.stderr
    assert "added: shared-job" in r.stdout
    assert "cross-machine" in _show(base_env, "shared-job")


def test_sync_apply_replaces_a_changed_job(base_env, tmp_path):
    _add_cross_machine_job(base_env)
    path = _write_manifest(tmp_path, _manifest_entry(cadence="daily@21:00"))
    r = _run(base_env, "ccsched-jobs", "sync", "--manifest", str(path), "--apply")
    assert r.returncode == 0, r.stderr
    assert "updated: shared-job" in r.stdout
    assert "daily@21:00" in _show(base_env, "shared-job")


def test_sync_leaves_an_unchanged_job_alone(base_env, tmp_path):
    _add_cross_machine_job(base_env)
    path = _write_manifest(tmp_path, _manifest_entry())
    r = _run(base_env, "ccsched-jobs", "sync", "--manifest", str(path), "--apply")
    assert r.returncode == 0, r.stderr
    assert "unchanged: shared-job" in r.stdout


def test_sync_never_touches_a_local_scope_job(base_env, tmp_path):
    r = _ccsched(
        base_env, "add", "--id", "local-job", "--cadence", "daily@09:00",
        "--timeout", "60s", "--command", "true",
    )
    assert r.returncode == 0, r.stderr
    path = _write_manifest(tmp_path, _manifest_entry())
    r = _run(base_env, "ccsched-jobs", "sync", "--manifest", str(path), "--apply")
    assert r.returncode == 0, r.stderr
    assert "local-job" not in r.stdout
    out = _show(base_env, "local-job")
    assert "daily@09:00" in out
    assert "scope:" in out and "local" in out


def test_sync_refuses_to_touch_a_bundled_job(base_env, tmp_path):
    """Bundled jobs stay on the ccsched-jobs install path (spec section 6). A manifest
    entry claiming one of their ids must fail loudly rather than be skipped, or the
    operator would believe a sync applied when it did not."""
    path = _write_manifest(tmp_path, _manifest_entry(job_id="pdata-verify-all"))
    r = _run(base_env, "ccsched-jobs", "sync", "--manifest", str(path), "--apply")
    assert r.returncode == 2
    assert "pdata-verify-all" in r.stderr


def test_sync_preserves_a_locally_disabled_job_on_update(base_env, tmp_path):
    _add_cross_machine_job(base_env)
    assert _ccsched(base_env, "disable", "shared-job").returncode == 0
    path = _write_manifest(tmp_path, _manifest_entry(timeout="90s"))
    r = _run(base_env, "ccsched-jobs", "sync", "--manifest", str(path), "--apply")
    assert r.returncode == 0, r.stderr
    out = _show(base_env, "shared-job")
    assert "90s" in out
    assert "enabled:   false" in out.replace("enabled:", "enabled:  ").replace("  ", " ").replace(
        "enabled: ", "enabled:   "
    ) or "false" in out


def test_sync_errors_clearly_on_a_missing_manifest(base_env, tmp_path):
    r = _run(base_env, "ccsched-jobs", "sync", "--manifest", str(tmp_path / "absent.json"))
    assert r.returncode == 2
    assert "absent.json" in r.stderr


def test_sync_defaults_to_the_cccs_clone_path(base_env, tmp_path):
    """No --manifest means ~/repos/claude-code-config-sync/config/ccsched-jobs.json -
    the same location check-config-drift.sh hardcodes."""
    home = tmp_path / "home"
    env = dict(base_env)
    env["HOME"] = str(home)
    r = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "ccsched-jobs", "sync"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=env,
    )
    assert r.returncode == 2
    assert "repos/claude-code-config-sync/config/ccsched-jobs.json" in r.stderr


def test_sync_reports_an_unresolved_placeholder_naming_the_key(base_env, tmp_path):
    path = _write_manifest(
        tmp_path, _manifest_entry(command=["bash", "{{HOME_DIR}}/x.sh"])
    )
    env = dict(base_env)
    env["CCSCHED_MACHINE_VALUES"] = str(tmp_path / "absent-values.json")
    r = _run(env, "ccsched-jobs", "sync", "--manifest", str(path), "--apply")
    assert r.returncode == 2
    assert "HOME_DIR" in r.stderr


def test_sync_resolves_placeholders_from_the_machine_values_file(base_env, tmp_path):
    path = _write_manifest(
        tmp_path, _manifest_entry(command=["bash", "{{HOME_DIR}}/x.sh"])
    )
    values = tmp_path / "values.json"
    values.write_text(json.dumps({"HOME_DIR": "/home/alice"}))
    env = dict(base_env)
    env["CCSCHED_MACHINE_VALUES"] = str(values)
    r = _run(env, "ccsched-jobs", "sync", "--manifest", str(path), "--apply")
    assert r.returncode == 0, r.stderr
    assert "/home/alice/x.sh" in _show(env, "shared-job")
```

Simplify `test_sync_preserves_a_locally_disabled_job_on_update`'s final assertion to a single
robust check against `ccsched show`'s aligned output, e.g.:

```python
    assert any(
        line.startswith("enabled:") and line.split(":", 1)[1].strip() == "false"
        for line in out.splitlines()
    )
```

Use that form rather than the string-mangling version above.

`HOME` is overridden in `test_sync_defaults_to_the_cccs_clone_path` — `Path.home()` reads
`HOME` on POSIX, and `base_env` already pins `CCST_DATA_HOME` to a tmp dir so no real state is
touched. If that test proves flaky across platforms, drop it and rely on
`test_default_manifest_path_points_at_the_cccs_clone` in `tests/scheduler/test_manifest.py`,
which covers the same contract at the unit level.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_ccsched_jobs_cli.py -q`
Expected: FAIL — `invalid choice: 'sync'`.

- [ ] **Step 3: Implement**

In `ccst.py`, after `_cmd_ccsched_jobs_export`:

```python
def _cmd_ccsched_jobs_sync(args: argparse.Namespace) -> int:
    """Reconcile local cross-machine jobs against the CCCS manifest (spec section 6).

    Unlike `ccsched-jobs install`, which is install-only-if-missing because a human may
    have hand-edited a bundled job, this command is allowed to *replace* an existing
    job: the manifest is the declared source of truth for cross-machine jobs, and a
    sync that could never update one would be pointless. That is also why it is a
    separate verb rather than a flag on install - the two have opposite policies.

    Never touches local-scope jobs, and refuses outright (rather than skipping) if the
    manifest claims a bundled job's id."""
    from cc_session_tools.lib.scheduler import bundled_jobs, machine_values, manifest, registry
    from cc_session_tools.lib.scheduler.jobspec import JobSpec

    manifest_path = (
        Path(args.manifest).expanduser() if args.manifest else manifest.default_manifest_path()
    )
    values_path = machine_values.machine_values_path()
    try:
        values = machine_values.load_machine_values(values_path)
        specs = manifest.parse_manifest(manifest_path, values)
    except machine_values.MachineValuesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except manifest.ManifestError as exc:
        print(f"error: {exc} (machine values file: {values_path})", file=sys.stderr)
        return 2

    bundled_ids = {job.job_id for job in bundled_jobs.BUNDLED_CCSCHED_JOBS}
    collisions = sorted(spec.job_id for spec in specs if spec.job_id in bundled_ids)
    if collisions:
        print(
            f"error: manifest {manifest_path} claims job id(s) owned by CCST's bundled "
            f"jobs: {', '.join(collisions)}. Bundled jobs are provisioned by "
            "'ccst ccsched-jobs install'; remove them from the manifest.",
            file=sys.stderr,
        )
        return 2

    local = {spec.job_id: spec for spec in registry.load_registry()}
    print(f"Manifest: {manifest_path}")
    for spec in specs:
        current = local.get(spec.job_id)
        if current is None:
            if args.apply:
                registry.add_job(spec)
                print(f"  added: {spec.job_id}")
            else:
                print(f"  would add: {spec.job_id}")
            continue
        # enabled is per-machine state the manifest cannot express, so it is neither
        # compared nor overwritten - see lib/scheduler/manifest.py's module docstring.
        desired = replace(spec, enabled=current.enabled)
        if desired == current:
            print(f"  unchanged: {spec.job_id}")
            continue
        if args.apply:
            registry.replace_job(desired)
            print(f"  updated: {spec.job_id}")
        else:
            print(f"  would update: {spec.job_id}")

    if not specs:
        print("  manifest lists no cross-machine jobs")
    if not args.apply:
        print("\nDry run — re-run with --apply to apply the change(s)")
    return 0
```

`replace` is `dataclasses.replace`; add `from dataclasses import replace` to `ccst.py`'s imports
if it is not already there (check first). The unused `JobSpec` import in the function body should
be removed — only import what the body actually uses.

Register the subparser next to `install` and `export`:

```python
    ccsched_jobs_sync_parser = ccsched_jobs_sub.add_parser(
        "sync",
        help=(
            "Reconcile cross-machine jobs against the claude-code-config-sync manifest "
            "(dry run by default). Adds a missing job and replaces a changed one; never "
            "touches local-scope or bundled jobs."
        ),
    )
    ccsched_jobs_sync_parser.add_argument(
        "--manifest", default=None, metavar="PATH",
        help=(
            "Manifest location (default: "
            "~/repos/claude-code-config-sync/config/ccsched-jobs.json)"
        ),
    )
    ccsched_jobs_sync_parser.add_argument(
        "--apply", action="store_true", help="Apply changes (default: dry run)",
    )
```

Extend the dispatch with the `sync` verb, matching the block's existing structure.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_ccsched_jobs_cli.py -q`
Expected: PASS, all of them (including the round-trip test from Task 7 with its `xfail` marker
now removed).

- [ ] **Step 5: Full suite + commit**

```bash
uv run pytest -q
```
Expected: PASS.

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_ccsched_jobs_cli.py
git commit -m "feat(ccst): add ccsched-jobs sync for the CCCS-to-local direction

install is deliberately install-only-if-missing so it never stomps a hand-edited
job. Cross-machine jobs need the opposite policy - a source of truth that can
never update an existing definition syncs nothing - so this is a separate verb
rather than a flag that would quietly change install's contract."
```

---

## Task 9: document the new commands in the README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find the existing ccsched / ccst documentation**

Run: `grep -n "ccsched" README.md | head -40`

Read the surrounding section so the new prose matches the file's existing structure, heading
depth and voice. If `README.md` documents `ccst ccsched-jobs install`, the new verbs belong in
the same place.

- [ ] **Step 2: Write the documentation**

Cover, matching the file's established style:

- `ccsched add|edit --scope {local,cross-machine}` and what the two scopes mean.
- `ccst ccsched-jobs export <job-id>` — prints a manifest entry to stdout; the operator pastes it
  into `claude-code-config-sync/config/ccsched-jobs.json` and commits by hand; it does not
  reverse `{{PLACEHOLDER}}` templating.
- `ccst ccsched-jobs sync [--manifest PATH] [--apply]` — dry run by default; adds missing and
  replaces changed cross-machine jobs; never touches local-scope or bundled jobs; preserves a
  locally-disabled job's disabled state.
- `~/.claude/ccsched-machine-values.json` — a flat JSON string→string map, deliberately not
  git-tracked, resolving `{{PLACEHOLDER}}` tokens in a cross-machine job's command at sync time.
  Show a short example using a fictional path such as `/home/alice/scripts`. **No real home
  directory paths.**

- [ ] **Step 3: Verify no personal identifiers**

Run: `git diff README.md | grep -nE "chris|Chris|cfog|/home/chris|/mnt/c/Users" || echo CLEAN`
Expected: `CLEAN`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document ccsched job scope and the CCCS manifest sync commands"
```

---

## Task 10: release 2.2.0

Independent of the feature above; do it only once Tasks 1-9 are committed and `uv run pytest -q`
is green.

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Gather the real history for the missing entries**

`CHANGELOG.md` has no entries for 2.0.0, 2.1.0 or 2.1.1 — the file jumps from 1.4.1 to the
2.1.1 entry this branch already added under `[Unreleased]`. Reconstruct them from actual history
only; invent nothing:

```bash
git log v1.4.1..v2.0.0 --oneline
git log v2.0.0..v2.1.0 --oneline
git log v2.1.0..v2.1.1 --oneline
```

For each PR merge commit in those ranges, run `git show <sha>` and read the merge message /
PR description for the "why". Also read the release tag messages:
`git tag -n99 v2.0.0 v2.1.0 v2.1.1`.

- [ ] **Step 2: Backfill 2.0.0, 2.1.0 and 2.1.1**

Match the house style already used by the existing entries: a Keep-a-Changelog section header
(`## [X.Y.Z] - YYYY-MM-DD`), grouped `### Added` / `### Changed` / `### Fixed` subsections, each
bullet a bold one-line summary followed by why it mattered and what the impact was. Read the
existing 1.4.1 and 2.1.1 entries first and copy their shape exactly.

Date each release from its tag: `git log -1 --format=%ad --date=short v2.0.0` (and the same for
the other two).

- [ ] **Step 3: Consolidate `[Unreleased]` into a 2.2.0 entry**

Run `date +%Y-%m-%d` for the date. Create `## [2.2.0] - <that date>` holding everything currently
under `[Unreleased]` plus this branch's work:

- Fixed: bash-hard-deny false-positive warning (`ebce01e`).
- Fixed: scheduler catch-up digest surfaced via `systemMessage`, not just model context
  (`733b03a`).
- Fixed: message-delivery digest surfaced via `systemMessage` too (`56b06d9`).
- Changed/Fixed: `skills/` and `config/` relocated under `src/cc_session_tools/` and packaged as
  wheel data, so an installed wheel actually ships them (`2c9ff02`, `aad089e`).
- Added: ccsched job `scope`, `{{PLACEHOLDER}}` machine-values templating,
  `ccst ccsched-jobs export`, `ccst ccsched-jobs sync` — this plan's work.

Leave `## [Unreleased]` above it with only its header, matching how the file already handles a
just-released version.

Verify the exact commit list on this branch with:
`git log origin/main..HEAD --oneline`

- [ ] **Step 4: Bump the version and regenerate the lockfile**

Set `version = "2.2.0"` in `pyproject.toml` (currently `2.1.1`). Minor bump: everything here is
additive — a new optional field with a backwards-compatible default, new CLI flags, new
subcommands — with no on-disk format anything old cannot read (the `scope` column has a DEFAULT,
and older code simply does not SELECT it).

```bash
uv lock
```

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest -q
grep -n "^## \[" CHANGELOG.md
grep -n '^version' pyproject.toml
```

Expected: suite green; `CHANGELOG.md` shows an unbroken `[Unreleased]`, `[2.2.0]`, `[2.1.1]`,
`[2.1.0]`, `[2.0.0]`, `[1.4.1]`, … sequence; version is `2.2.0`.

```bash
git add CHANGELOG.md pyproject.toml uv.lock
git commit -m "build: release 2.2.0 and backfill the missing 2.0.0-2.1.1 changelog entries

Three releases shipped without changelog entries, leaving anyone reading the
file to diff tags by hand to find out what changed. 2.2.0 is a minor bump: every
change in it is additive, and the new scope column carries a default so existing
rows stay readable."
```

---

## Task 11: full verification and PR

- [ ] **Step 1: Run every configured check**

```bash
uv run pytest -q
```
Expected: exit 0, no failures, no skips introduced by this work.

CI (`.github/workflows/ci.yml`) runs exactly `uv run pytest -q` across
{ubuntu, macos} x py{3.11, 3.12, 3.13}, plus an `install-check` job that does `uv tool install .`
and runs `--version` on each CLI. Do **not** run `uv tool install` from this worktree — it would
repoint the global install (see `.claude/CLAUDE.md`). Instead sanity-check the CLI surface in
place:

```bash
uv run python -m cc_session_tools.cli.ccst ccsched-jobs --help
uv run python -m cc_session_tools.cli.ccst ccsched-jobs sync --help
uv run python -m cc_session_tools.cli.ccsched add --help
```

mypy is a dev dependency but is **not** a CI gate, and it has pre-existing failures on this
branch unrelated to this work (`src/cc_session_tools/cli/ccs.py`, plus a duplicate-`conftest`
module error when run over all of `src`). Hold only the new/modified modules to mypy-clean:

```bash
uv run mypy src/cc_session_tools/lib/scheduler
```
Expected: no errors in `lib/scheduler`. Do not attempt to fix the pre-existing `ccs.py` errors —
that is unrelated work and would land unreviewed alongside a release.

- [ ] **Step 2: Check for personal identifiers across the whole branch diff**

```bash
git diff origin/main..HEAD | grep -nE "chris|Chris|cfog|/home/chris|/mnt/c/Users|@gmail|@physicsx" || echo CLEAN
```
Expected: `CLEAN`. If anything matches, remove it before pushing.

- [ ] **Step 3: Push**

```bash
git push -u origin f/1.4.2
```
Never force-push.

- [ ] **Step 4: Open the PR (do not merge)**

```bash
gh pr create --base main --head f/1.4.2 --title "..." --body "..."
```

The body should cover:
- the four ported fixes/refactors already on the branch,
- the new ccsched scope + export/sync feature, linking the spec path
  `docs/superpowers/specs/2026-08-12-ccsched-cross-machine-jobs-design.md` and the plan path
  `docs/superpowers/plans/2026-08-12-ccsched-scope-and-sync-ccst-side.md`,
- that §2/§5 of the same spec are implemented by a companion PR in `claude-code-config-sync`
  (describe it; there is no URL to link),
- the 2.2.0 release and backfilled changelog entries,
- test status.

Leave the PR open. Do not merge.
