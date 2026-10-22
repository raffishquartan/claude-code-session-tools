# Plan 1: Core sync — running-session gate + `ccst pdata sync push/pull` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Read
> `docs/superpowers/plans/2026-08-02-multi-laptop-sync-00-overview.md` first — it has binding
> cross-plan decisions this plan builds on (module ownership, env vars, timestamp format, the
> `db.backup_to()` copy primitive).

**Goal:** let Chris run `ccst pdata sync push --project NAME` on one laptop and `ccst pdata sync
pull --project NAME` (or `pull --all-projects`) on the other, moving one project's
`project-db/<project>.db` between machines via timestamped snapshots stored inside the project's
own synced folder — safely, never mid-write.

**Architecture:** a new `lib/running_gate.py` answers "is it safe to copy this .db file right
now?" (no `claude` process running anywhere on the machine, no sign of an open connection on the
file). A new `lib/pdata/sync.py` builds on that gate plus the existing `lib/db.py::backup_to()`
primitive to create/list/restore/prune snapshots. `cli/ccst.py` gains a `pdata sync push|pull`
verb pair following the exact `pdata schema <subverb>` pattern already in that file.

**Tech Stack:** Python 3.11+, stdlib `argparse`/`subprocess`/`sqlite3`, `pytest`.

---

## File Structure

- Create: `src/cc_session_tools/lib/running_gate.py` — the safety gate (new, no existing helper to extend)
- Create: `tests/test_running_gate.py`
- Create: `src/cc_session_tools/lib/pdata/sync.py` — snapshot create/list/restore/prune
- Create: `tests/pdata/test_sync.py`
- Modify: `src/cc_session_tools/cli/ccst.py` — add `pdata sync push`/`pdata sync pull` parser + handlers + dispatch
- Create: `tests/test_ccst_pdata_sync_cli.py`

---

### Task 1: `lib/running_gate.py` — safety gate

**Files:**
- Create: `src/cc_session_tools/lib/running_gate.py`
- Test: `tests/test_running_gate.py`

No existing code in this repo checks "is a process running" or "is this file open" — this is
new, from scratch (confirmed via full-repo search: no `lsof`, `psutil`, or `pgrep` anywhere). Two
signals combine: a mandatory `pgrep -x claude` check (raises if `pgrep` itself is missing — this
is not an optional signal) and a best-effort WAL/SHM-sidecar + `lsof` check (skipped, not failed,
if `lsof` is absent).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_running_gate.py
from __future__ import annotations

import pytest

from cc_session_tools.lib import running_gate


class _FakeCompleted:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


def _which_all_present(name: str) -> str | None:
    return f"/usr/bin/{name}"


def test_ok_when_no_claude_running_and_no_sidecars(tmp_path, monkeypatch):
    monkeypatch.setattr(running_gate.shutil, "which", _which_all_present)
    monkeypatch.setattr(running_gate.subprocess, "run", lambda cmd, **kw: _FakeCompleted(1))
    db_path = tmp_path / "demo.db"
    db_path.write_text("")
    result = running_gate.check_safe_to_copy(db_path)
    assert result.ok
    assert result.reasons == ()


def test_blocks_when_claude_process_running(tmp_path, monkeypatch):
    monkeypatch.setattr(running_gate.shutil, "which", _which_all_present)

    def fake_run(cmd, **kw):
        return _FakeCompleted(0 if cmd[0] == "pgrep" else 1)

    monkeypatch.setattr(running_gate.subprocess, "run", fake_run)
    db_path = tmp_path / "demo.db"
    db_path.write_text("")
    result = running_gate.check_safe_to_copy(db_path)
    assert not result.ok
    assert any("claude" in r for r in result.reasons)


def test_blocks_on_nonempty_wal_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(running_gate.shutil, "which", _which_all_present)
    monkeypatch.setattr(running_gate.subprocess, "run", lambda cmd, **kw: _FakeCompleted(1))
    db_path = tmp_path / "demo.db"
    db_path.write_text("")
    (tmp_path / "demo.db-wal").write_bytes(b"x")
    result = running_gate.check_safe_to_copy(db_path)
    assert not result.ok
    assert any("wal" in r.lower() for r in result.reasons)


def test_ignores_empty_wal_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(running_gate.shutil, "which", _which_all_present)
    monkeypatch.setattr(running_gate.subprocess, "run", lambda cmd, **kw: _FakeCompleted(1))
    db_path = tmp_path / "demo.db"
    db_path.write_text("")
    (tmp_path / "demo.db-wal").write_bytes(b"")
    result = running_gate.check_safe_to_copy(db_path)
    assert result.ok


def test_blocks_when_lsof_reports_open_handle(tmp_path, monkeypatch):
    monkeypatch.setattr(running_gate.shutil, "which", _which_all_present)

    def fake_run(cmd, **kw):
        return _FakeCompleted(1 if cmd[0] == "pgrep" else 0)

    monkeypatch.setattr(running_gate.subprocess, "run", fake_run)
    db_path = tmp_path / "demo.db"
    db_path.write_text("")
    result = running_gate.check_safe_to_copy(db_path)
    assert not result.ok
    assert any("lsof" in r for r in result.reasons)


def test_skips_lsof_signal_when_lsof_not_installed(tmp_path, monkeypatch):
    def fake_which(name):
        return None if name == "lsof" else f"/usr/bin/{name}"

    monkeypatch.setattr(running_gate.shutil, "which", fake_which)
    monkeypatch.setattr(running_gate.subprocess, "run", lambda cmd, **kw: _FakeCompleted(1))
    db_path = tmp_path / "demo.db"
    db_path.write_text("")
    result = running_gate.check_safe_to_copy(db_path)
    assert result.ok


def test_raises_when_pgrep_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(running_gate.shutil, "which", lambda name: None)
    db_path = tmp_path / "demo.db"
    db_path.write_text("")
    with pytest.raises(RuntimeError, match="pgrep"):
        running_gate.check_safe_to_copy(db_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_running_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.running_gate'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/running_gate.py
"""Safety gate for copying a live .db file (spec §4.4): refuse if a `claude`
process is running anywhere on this machine, or if the file shows a sign of
an open connection. No existing helper does either check — see
db.backup_to() for the actual copy primitive this gate protects; the gate is
an additional, coarser safety net on top of it, not a replacement.

Design note: the process check is machine-wide ("no claude process running
anywhere"), not file-specific — this means a caller cannot invoke this
command from inside a live Claude Code session (its own `claude` process
trips the check). This is the settled spec decision (§4.4), surfaced here so
callers can give a clear error rather than a confusing one.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GateResult:
    ok: bool
    reasons: tuple[str, ...] = ()


def _claude_processes_running() -> bool:
    if shutil.which("pgrep") is None:
        raise RuntimeError(
            "running_gate: 'pgrep' not found on PATH — cannot verify no claude "
            "process is running. Install pgrep (procps) before running this command."
        )
    result = subprocess.run(["pgrep", "-x", "claude"], capture_output=True)
    return result.returncode == 0


def _wal_or_shm_nonempty(db_path: Path) -> bool:
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists() and sidecar.stat().st_size > 0:
            return True
    return False


def _open_via_lsof(db_path: Path) -> bool | None:
    """Best-effort: True/False if lsof ran, None if lsof isn't installed —
    this signal is then skipped, not treated as a failure (spec §4.4's
    "and/or an open file handle via lsof")."""
    if shutil.which("lsof") is None:
        return None
    for candidate in (db_path, db_path.with_name(db_path.name + "-wal"),
                      db_path.with_name(db_path.name + "-shm")):
        if not candidate.exists():
            continue
        if subprocess.run(["lsof", str(candidate)], capture_output=True).returncode == 0:
            return True
    return False


def check_safe_to_copy(db_path: Path) -> GateResult:
    """No override here — a caller that needs one (the pull staleness check)
    implements its own; per spec only §4.5's staleness check has --force."""
    reasons: list[str] = []
    if _claude_processes_running():
        reasons.append(
            "a 'claude' process is running on this machine — quit every Claude "
            "Code session (including one invoking this command) before syncing"
        )
    if _wal_or_shm_nonempty(db_path):
        reasons.append(
            f"{db_path.name} has a non-empty -wal/-shm sidecar file — "
            "a connection may still be open"
        )
    if _open_via_lsof(db_path):
        reasons.append(f"lsof reports {db_path.name} (or its -wal/-shm) is open by another process")
    return GateResult(ok=not reasons, reasons=tuple(reasons))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_running_gate.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/running_gate.py tests/test_running_gate.py
git commit -m "feat: add running-session safety gate for DB copies"
```

---

### Task 2: `lib/pdata/sync.py` — snapshot create/list/discover

**Files:**
- Create: `src/cc_session_tools/lib/pdata/sync.py`
- Test: `tests/pdata/test_sync.py`
- Modify: `src/cc_session_tools/lib/pdata/init_paths.py:26-29` (`EXCLUDED_DIR_NAMES`) — see Step 3b
  below

- [ ] **Step 1: Write the failing tests**

```python
# tests/pdata/test_sync.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import sync


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "project-db"))
    monkeypatch.setenv("CCST_PROJECTS_ROOT", str(tmp_path / "projects"))
    return tmp_path


@pytest.fixture(autouse=True)
def _gate_always_ok(monkeypatch):
    from cc_session_tools.lib import running_gate
    monkeypatch.setattr(
        running_gate, "check_safe_to_copy", lambda db_path: running_gate.GateResult(ok=True)
    )


def _write_project_db(project: str) -> Path:
    from cc_session_tools.lib.pdata import repository
    conn = repository.connect(project)
    conn.close()
    from cc_session_tools.lib.pdata import store
    return store.db_path(project)


def test_snapshot_root_matches_spec_layout(tmp_path):
    root = sync.snapshot_root("demo")
    assert root == tmp_path / "projects" / "demo" / ".pdata-sync" / "demo"


def test_list_snapshots_empty_when_none_taken():
    assert sync.list_snapshots("demo") == []


def test_create_snapshot_writes_a_new_timestamped_file():
    _write_project_db("demo")
    snap = sync.create_snapshot("demo")
    assert snap.path.is_file()
    assert snap.path.parent.name == snap.timestamp
    assert snap.path.name == "demo.db"


def test_create_snapshot_raises_for_unknown_project():
    with pytest.raises(ValueError, match="no data store"):
        sync.create_snapshot("never-touched")


def test_create_snapshot_raises_when_gate_blocks(monkeypatch):
    from cc_session_tools.lib import running_gate
    _write_project_db("demo")
    monkeypatch.setattr(
        running_gate, "check_safe_to_copy",
        lambda db_path: running_gate.GateResult(ok=False, reasons=("claude is running",)),
    )
    with pytest.raises(RuntimeError, match="claude is running"):
        sync.create_snapshot("demo")


def test_list_snapshots_reverse_chronological():
    _write_project_db("demo")
    first = sync.create_snapshot("demo")
    import time
    time.sleep(1.1)  # ensure a distinct second-resolution timestamp
    second = sync.create_snapshot("demo")
    snapshots = sync.list_snapshots("demo")
    assert [s.timestamp for s in snapshots] == [second.timestamp, first.timestamp]


def test_discover_synced_projects_finds_only_projects_with_snapshots(tmp_path):
    _write_project_db("has-snapshot")
    _write_project_db("no-snapshot")
    sync.create_snapshot("has-snapshot")
    assert sync.discover_synced_projects() == ["has-snapshot"]


def test_discover_synced_projects_empty_when_no_projects_root():
    assert sync.discover_synced_projects() == []


def test_create_snapshot_disambiguates_same_second_collision(monkeypatch):
    """Two pushes within the same wall-clock second must not silently
    overwrite each other — spec §4.5 says push always writes a NEW
    snapshot."""
    _write_project_db("demo")
    monkeypatch.setattr(sync, "_timestamp", lambda: "20260101T000000Z")
    first = sync.create_snapshot("demo")
    second = sync.create_snapshot("demo")
    assert first.timestamp != second.timestamp
    assert first.path.is_file()
    assert second.path.is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pdata/test_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cc_session_tools.lib.pdata.sync'`

- [ ] **Step 3: Write the implementation**

```python
# src/cc_session_tools/lib/pdata/sync.py
"""Snapshot push/pull for one project's project-db/<project>.db (spec
§4.5-4.6). Snapshots live inside the project's own source folder under
init_paths.default_projects_root(), riding the project's existing
OneDrive/GitHub sync — nothing new to configure. Every copy goes through
db.backup_to(), never shutil.copy (see plan-00-overview.md §3)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib import db, running_gate
from cc_session_tools.lib.pdata import init_paths, store

SYNC_DIRNAME = ".pdata-sync"


def _timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def snapshot_root(project: str) -> Path:
    """~/cc/<project>/.pdata-sync/<project>/ — spec §4.6's exact layout."""
    store.validate_project_name(project)
    return init_paths.default_projects_root() / project / SYNC_DIRNAME / project


@dataclass(frozen=True, slots=True)
class Snapshot:
    timestamp: str
    path: Path  # .../.pdata-sync/<project>/<timestamp>/<project>.db


def list_snapshots(project: str) -> list[Snapshot]:
    """Reverse-chronological (newest first) — the fixed-width timestamp
    format sorts correctly as a plain string sort."""
    root = snapshot_root(project)
    if not root.is_dir():
        return []
    out = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        db_file = child / f"{project}.db"
        if db_file.is_file():
            out.append(Snapshot(timestamp=child.name, path=db_file))
    return sorted(out, key=lambda s: s.timestamp, reverse=True)


def create_snapshot(project: str) -> Snapshot:
    """Run the running-session gate against the live project .db, then write
    a new timestamped snapshot. Never overwrites a previous snapshot (spec
    §4.5 — push always writes a new one, doubling as backup history) — a
    same-second collision (two pushes within one wall-clock second) gets a
    numeric suffix rather than silently clobbering the earlier snapshot."""
    source = store.db_path(project)
    if not source.is_file():
        raise ValueError(f"no data store for project {project!r} (looked for {source})")
    gate = running_gate.check_safe_to_copy(source)
    if not gate.ok:
        raise RuntimeError(
            "refusing to snapshot while unsafe:\n" + "\n".join(f"  - {r}" for r in gate.reasons)
        )
    root = snapshot_root(project)
    timestamp = _timestamp()
    if (root / timestamp).exists():
        suffix = 2
        while (root / f"{timestamp}-{suffix}").exists():
            suffix += 1
        timestamp = f"{timestamp}-{suffix}"
    dest = root / timestamp / f"{project}.db"
    db.backup_to(source, dest)
    return Snapshot(timestamp=timestamp, path=dest)


def discover_synced_projects() -> list[str]:
    """Every project with at least one push snapshot on disk — used by `pull
    --all-projects`, which must find projects that have NO local .db yet on
    this machine (unlike verify.discover_projects(), which is keyed on an
    existing local .db and would miss exactly the projects a fresh laptop
    needs to catch up on)."""
    root = init_paths.default_projects_root()
    if not root.is_dir():
        return []
    out = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and (entry / SYNC_DIRNAME / entry.name).is_dir():
            out.append(entry.name)
    return out
```

- [ ] **Step 3b: Exclude `.pdata-sync` from `ccst pdata init`'s classifier walk**

`init_paths.py`'s `EXCLUDED_DIR_NAMES` (`init_paths.py:26-29`) lists directory names the `ccst
pdata init` classifier never walks into — repo/tool bookkeeping, not project content. Without
this, re-running `pdata init` on a project after it has push snapshots would walk into
`.pdata-sync/<project>/<timestamp>/<project>.db` files and propose them as unclassified content,
growing noisier with every push. Add `sync.SYNC_DIRNAME` to the set:

```python
# src/cc_session_tools/lib/pdata/init_paths.py — modify EXCLUDED_DIR_NAMES (init_paths.py:26-29)
EXCLUDED_DIR_NAMES = frozenset({
    ".git", ".claude", "cc-sessions", MIGRATED_ARCHIVE_DIRNAME, REHEARSAL_DB_DIRNAME,
    REHEARSAL_BACKUP_DIRNAME, ".pdata-sync",
})
```

(A bare string literal here, not an import of `sync.SYNC_DIRNAME` — `init_paths.py` is imported
by `sync.py`, so importing back from `sync.py` would create a circular import. `".pdata-sync"`
already appears as a literal in this same file's own `SYNC_DIRNAME` peer constants, matching the
existing convention.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pdata/test_sync.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/sync.py src/cc_session_tools/lib/pdata/init_paths.py tests/pdata/test_sync.py
git commit -m "feat: add pdata snapshot create/list/discover"
```

---

### Task 3: `lib/pdata/sync.py` — `restore_snapshot` + `prune_snapshots`

**Files:**
- Modify: `src/cc_session_tools/lib/pdata/sync.py`
- Modify: `tests/pdata/test_sync.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/pdata/test_sync.py

def test_restore_snapshot_copies_snapshot_over_live_db():
    from cc_session_tools.lib.pdata import store
    _write_project_db("demo")
    snap = sync.create_snapshot("demo")
    store.db_path("demo").unlink()  # simulate a fresh laptop with no local db yet
    sync.restore_snapshot("demo", snap)
    assert store.db_path("demo").is_file()


def test_restore_snapshot_refuses_when_snapshot_older_than_local():
    from cc_session_tools.lib.pdata import store
    import os
    import time
    _write_project_db("demo")
    snap = sync.create_snapshot("demo")
    # Make the live .db look newer than the snapshot just taken.
    live = store.db_path("demo")
    os.utime(live, (time.time() + 10, time.time() + 10))
    with pytest.raises(sync.StaleRestoreError):
        sync.restore_snapshot("demo", snap)


def test_restore_snapshot_force_bypasses_staleness_check():
    from cc_session_tools.lib.pdata import store
    import os
    import time
    _write_project_db("demo")
    snap = sync.create_snapshot("demo")
    live = store.db_path("demo")
    os.utime(live, (time.time() + 10, time.time() + 10))
    sync.restore_snapshot("demo", snap, force=True)  # must not raise


def test_restore_snapshot_raises_when_gate_blocks(monkeypatch):
    from cc_session_tools.lib import running_gate
    _write_project_db("demo")
    snap = sync.create_snapshot("demo")
    monkeypatch.setattr(
        running_gate, "check_safe_to_copy",
        lambda db_path: running_gate.GateResult(ok=False, reasons=("claude is running",)),
    )
    with pytest.raises(RuntimeError, match="claude is running"):
        sync.restore_snapshot("demo", snap)


def test_prune_snapshots_deletes_oldest_beyond_keep():
    import time
    _write_project_db("demo")
    old = sync.create_snapshot("demo")
    time.sleep(1.1)
    new = sync.create_snapshot("demo")
    deleted = sync.prune_snapshots("demo", keep=1)
    assert [d.timestamp for d in deleted] == [old.timestamp]
    assert [s.timestamp for s in sync.list_snapshots("demo")] == [new.timestamp]
    assert not old.path.exists()


def test_prune_snapshots_keep_zero_deletes_all():
    _write_project_db("demo")
    sync.create_snapshot("demo")
    deleted = sync.prune_snapshots("demo", keep=0)
    assert len(deleted) == 1
    assert sync.list_snapshots("demo") == []


def test_prune_snapshots_rejects_negative_keep():
    with pytest.raises(ValueError, match="keep must be"):
        sync.prune_snapshots("demo", keep=-1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pdata/test_sync.py -v`
Expected: FAIL with `AttributeError: module 'cc_session_tools.lib.pdata.sync' has no attribute 'restore_snapshot'`

- [ ] **Step 3: Write the implementation**

```python
# append to src/cc_session_tools/lib/pdata/sync.py

class StaleRestoreError(RuntimeError):
    """Raised when the chosen snapshot is older than the local .db and force wasn't passed."""


def restore_snapshot(project: str, snapshot: Snapshot, *, force: bool = False) -> None:
    """Restore snapshot over the live project .db. Refuses (StaleRestoreError)
    if the snapshot is older than the local .db's mtime, unless force=True
    (spec §4.5's staleness check). No staleness comparison at all when no
    local .db exists yet (first pull onto a fresh laptop)."""
    dest = store.db_path(project)
    if dest.is_file() and not force:
        if snapshot.path.stat().st_mtime < dest.stat().st_mtime:
            raise StaleRestoreError(
                f"snapshot {snapshot.timestamp} is older than the local database "
                f"for {project!r} — pass force=True to restore anyway"
            )
    gate = running_gate.check_safe_to_copy(dest)
    if not gate.ok:
        raise RuntimeError(
            "refusing to restore while unsafe:\n" + "\n".join(f"  - {r}" for r in gate.reasons)
        )
    db.backup_to(snapshot.path, dest)


def prune_snapshots(project: str, *, keep: int) -> list[Snapshot]:
    """Delete every snapshot beyond the `keep` most recent. Returns the
    deleted Snapshots."""
    if keep < 0:
        raise ValueError(f"keep must be >= 0, got {keep}")
    to_delete = list_snapshots(project)[keep:]
    for snap in to_delete:
        snap.path.unlink()
        try:
            snap.path.parent.rmdir()
        except OSError:
            pass
    return to_delete
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/pdata/test_sync.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/lib/pdata/sync.py tests/pdata/test_sync.py
git commit -m "feat: add pdata snapshot restore and prune"
```

---

### Task 4: CLI wiring — `ccst pdata sync push`

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py` — parser (near `pdata_verify_parser`, `ccst.py:1926`), dispatch (`ccst.py:2130`), handler (near `_cmd_pdata_verify`, `ccst.py:1143`)
- Create: `tests/test_ccst_pdata_sync_cli.py`

Every CLI test in this task spawns `ccst` as a real subprocess (matching
`tests/test_ccst_pdata_verify_cli.py`), so `running_gate`'s internals can't be monkeypatched
directly — a fake-`pgrep`/`lsof` directory is prepended to the subprocess's `PATH` instead,
matching this repo's existing pattern of controlling external-tool behaviour via the `env` dict
passed to `subprocess.run`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ccst_pdata_sync_cli.py
from __future__ import annotations

import os
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
    env["CCST_PROJECT_DB_DIR"] = str(tmp_path / "project-db")
    env["CCST_PROJECTS_ROOT"] = str(tmp_path / "projects")
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    _write_stub(fakebin / "pgrep", 1)  # 1 = no claude process found
    _write_stub(fakebin / "lsof", 1)  # 1 = nothing has it open
    env["PATH"] = f"{fakebin}:{env['PATH']}"
    return env


@pytest.fixture
def claude_running_env(base_env, tmp_path):
    _write_stub(tmp_path / "fakebin" / "pgrep", 0)  # 0 = a claude process IS running
    return base_env


def test_sync_push_writes_snapshot_under_projects_root(base_env, tmp_path):
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "notes", "--content", "x")
    r = _run(base_env, "pdata", "sync", "push", "--project", "demo")
    assert r.returncode == 0, r.stderr
    snapshots = list((tmp_path / "projects" / "demo" / ".pdata-sync" / "demo").glob("*/demo.db"))
    assert len(snapshots) == 1
    assert "snapshot written to" in r.stdout


def test_sync_push_unknown_project_exits_two(base_env):
    r = _run(base_env, "pdata", "sync", "push", "--project", "never-touched")
    assert r.returncode == 2
    assert "no data store" in r.stderr.lower()


def test_sync_push_refuses_when_claude_running(claude_running_env):
    _run(claude_running_env, "pdata", "add", "--project", "demo", "--group", "notes", "--content", "x")
    r = _run(claude_running_env, "pdata", "sync", "push", "--project", "demo")
    assert r.returncode == 1
    assert "claude" in r.stderr.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_pdata_sync_cli.py -v`
Expected: FAIL — `argument verb: invalid choice: 'sync'` (parser doesn't know `sync` yet)

- [ ] **Step 3: Write the implementation**

Add the parser, right after the `verify` block (`ccst.py:1926-1938`, before the closing of the
`pdata` noun section at `ccst.py:1939`):

```python
    pdata_sync_parser = pdata_sub.add_parser(
        "sync", help="Push/pull a project's data store between laptops (spec §4.5-4.6)"
    )
    pdata_sync_sub = pdata_sync_parser.add_subparsers(dest="subverb", metavar="<subverb>")
    pdata_sync_sub.required = True

    pdata_sync_push_parser = pdata_sync_sub.add_parser(
        "push", help="Write a new timestamped snapshot of the local database"
    )
    pdata_sync_push_parser.add_argument("--project", required=True, metavar="NAME")

    pdata_sync_pull_parser = pdata_sync_sub.add_parser(
        "pull", help="Restore the local database from a snapshot"
    )
    pdata_sync_pull_target = pdata_sync_pull_parser.add_mutually_exclusive_group(required=True)
    pdata_sync_pull_target.add_argument("--project", metavar="NAME")
    pdata_sync_pull_target.add_argument(
        "--all-projects", action="store_true",
        help="Bulk-refresh every project with a snapshot on disk, auto-picking the latest",
    )
    pdata_sync_pull_parser.add_argument(
        "--force", action="store_true",
        help="Restore even if the chosen snapshot is older than the local database",
    )
```

Add the handler, right after `_cmd_pdata_verify` (`ccst.py:1164`):

```python
def _cmd_pdata_sync_push(args: argparse.Namespace) -> int:
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
    return 0
```

Add dispatch inside the `if args.noun == "pdata":` block, right after the `verify` dispatch
(`ccst.py:2130-2131`):

```python
        if args.verb == "sync":
            if args.subverb == "push":
                sys.exit(_cmd_pdata_sync_push(args))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_pdata_sync_cli.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_pdata_sync_cli.py
git commit -m "feat: add 'ccst pdata sync push'"
```

---

### Task 5: CLI wiring — `ccst pdata sync pull --project` (interactive picker + cleanup prompt)

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/test_ccst_pdata_sync_cli.py`

Non-interactive behaviour (the common case in tests and in scripted use) auto-picks the newest
snapshot and skips the cleanup prompt — matching the existing `ccr`/`ccs` convention of falling
back gracefully when `sys.stdin.isatty()` is `False`. Interactive-picker behaviour itself
(`pick_from_list` paging) is exercised with `monkeypatch` in a plain unit test alongside the CLI
tests, since `pick_from_list` reads from real `stdin` and a subprocess test can't drive that
without a pty.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_ccst_pdata_sync_cli.py

def test_sync_pull_project_restores_newest_snapshot_noninteractive(base_env, tmp_path):
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "notes", "--content", "x")
    _run(base_env, "pdata", "sync", "push", "--project", "demo")
    (tmp_path / "project-db" / "demo.db").unlink()  # simulate a fresh laptop
    r = _run(base_env, "pdata", "sync", "pull", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "project-db" / "demo.db").is_file()
    assert "restored snapshot" in r.stdout


def test_sync_pull_project_no_snapshots_exits_two(base_env):
    r = _run(base_env, "pdata", "sync", "pull", "--project", "never-pushed")
    assert r.returncode == 2
    assert "no snapshots" in r.stderr.lower()


def test_sync_pull_project_stale_without_force_exits_one(base_env, tmp_path):
    import os
    import time
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "notes", "--content", "x")
    _run(base_env, "pdata", "sync", "push", "--project", "demo")
    live = tmp_path / "project-db" / "demo.db"
    os.utime(live, (time.time() + 10, time.time() + 10))
    r = _run(base_env, "pdata", "sync", "pull", "--project", "demo")
    assert r.returncode == 1
    assert "--force" in r.stderr


def test_sync_pull_project_stale_with_force_succeeds(base_env, tmp_path):
    import os
    import time
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "notes", "--content", "x")
    _run(base_env, "pdata", "sync", "push", "--project", "demo")
    live = tmp_path / "project-db" / "demo.db"
    os.utime(live, (time.time() + 10, time.time() + 10))
    r = _run(base_env, "pdata", "sync", "pull", "--project", "demo", "--force")
    assert r.returncode == 0, r.stderr


def test_pick_snapshot_pages_through_more_than_five(monkeypatch):
    from cc_session_tools.cli import ccst
    from cc_session_tools.lib.pdata.sync import Snapshot

    snapshots = [Snapshot(timestamp=f"t{i}", path=Path(f"/nonexistent/{i}")) for i in range(7)]
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    picks = iter([5, 0])  # first page: "show older..." (index 5); second page: first item

    from cc_session_tools.lib import picker
    monkeypatch.setattr(picker, "pick_from_list", lambda labels: next(picks))

    chosen = ccst._pick_snapshot("demo", snapshots)
    assert chosen.timestamp == "t5"  # index 0 of the second page (offset 5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_pdata_sync_cli.py -v`
Expected: FAIL — `argument subverb: invalid choice` / `AttributeError: module has no attribute '_pick_snapshot'`

- [ ] **Step 3: Write the implementation**

Add near the top of `ccst.py` (module-level, alongside other CLI-private helpers) and the handler
next to `_cmd_pdata_sync_push`:

```python
_SYNC_PICKER_PAGE_SIZE = 5


def _pick_snapshot(project: str, snapshots: list) -> object | None:
    """Interactive picker: show the 5 most recent snapshots, with a 'show
    older' option to page through the rest (spec §4.6). Auto-picks the
    newest snapshot when stdin isn't a TTY."""
    if not sys.stdin.isatty():
        print(
            f"ccst pdata sync pull: non-interactive session, picking newest "
            f"snapshot ({snapshots[0].timestamp}) for {project!r}",
            file=sys.stderr,
        )
        return snapshots[0]

    from cc_session_tools.lib.picker import pick_from_list

    offset = 0
    while True:
        page = snapshots[offset:offset + _SYNC_PICKER_PAGE_SIZE]
        labels = [s.timestamp for s in page]
        has_more = offset + _SYNC_PICKER_PAGE_SIZE < len(snapshots)
        if has_more:
            labels.append("show older snapshots...")
        idx = pick_from_list(labels)
        if idx is None:
            return None
        if has_more and idx == len(labels) - 1:
            offset += _SYNC_PICKER_PAGE_SIZE
            continue
        return page[idx]


def _maybe_prune_snapshots(project: str, snapshots_before_restore: list) -> None:
    """Ask whether to prune old snapshots after a restore (spec §4.6). Skips
    silently when stdin isn't a TTY or there's nothing to prune."""
    if not sys.stdin.isatty() or len(snapshots_before_restore) <= 1:
        return
    from cc_session_tools.lib.pdata import sync

    try:
        raw = input(
            f"Prune old snapshots for {project!r}? Keep how many "
            f"(currently {len(snapshots_before_restore)}, Enter to skip): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not raw:
        return
    try:
        keep = int(raw)
    except ValueError:
        print("ccst pdata sync pull: not a number, skipping cleanup", file=sys.stderr)
        return
    deleted = sync.prune_snapshots(project, keep=keep)
    if deleted:
        print(f"  pruned {len(deleted)} old snapshot(s)")


def _cmd_pdata_sync_pull_project(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import sync

    snapshots = sync.list_snapshots(args.project)
    if not snapshots:
        print(f"ccst pdata sync pull: no snapshots found for project {args.project!r}", file=sys.stderr)
        return 2

    chosen = _pick_snapshot(args.project, snapshots)
    if chosen is None:
        return 0

    try:
        sync.restore_snapshot(args.project, chosen, force=args.force)
    except sync.StaleRestoreError as exc:
        print(f"ccst pdata sync pull: {exc}", file=sys.stderr)
        print("  pass --force to restore anyway", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ccst pdata sync pull: {exc}", file=sys.stderr)
        return 1
    print(f"{args.project}: restored snapshot {chosen.timestamp}")
    _maybe_prune_snapshots(args.project, snapshots)
    return 0


def _cmd_pdata_sync_pull(args: argparse.Namespace) -> int:
    if args.all_projects:
        return _cmd_pdata_sync_pull_all(args)
    return _cmd_pdata_sync_pull_project(args)
```

`_cmd_pdata_sync_pull_all` is added in Task 6 — leave its call as a forward reference (Python
resolves it at call time, not definition time, so this is safe to land now).

Extend the dispatch added in Task 4:

```python
        if args.verb == "sync":
            if args.subverb == "push":
                sys.exit(_cmd_pdata_sync_push(args))
            if args.subverb == "pull":
                sys.exit(_cmd_pdata_sync_pull(args))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_pdata_sync_cli.py -v`
Expected: 8 passed (3 from Task 4 + 5 new)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_pdata_sync_cli.py
git commit -m "feat: add 'ccst pdata sync pull --project'"
```

---

### Task 6: CLI wiring — `ccst pdata sync pull --all-projects`

**Files:**
- Modify: `src/cc_session_tools/cli/ccst.py`
- Modify: `tests/test_ccst_pdata_sync_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_ccst_pdata_sync_cli.py

def test_sync_pull_all_projects_reports_pulled_skipped_not_found(base_env, tmp_path):
    import os
    import time

    _run(base_env, "pdata", "add", "--project", "alpha", "--group", "notes", "--content", "x")
    _run(base_env, "pdata", "sync", "push", "--project", "alpha")
    (tmp_path / "project-db" / "alpha.db").unlink()

    _run(base_env, "pdata", "add", "--project", "beta", "--group", "notes", "--content", "y")
    _run(base_env, "pdata", "sync", "push", "--project", "beta")
    live_beta = tmp_path / "project-db" / "beta.db"
    os.utime(live_beta, (time.time() + 10, time.time() + 10))  # local newer -> skip

    _run(base_env, "pdata", "add", "--project", "gamma", "--group", "notes", "--content", "z")
    # gamma never pushed -> "no snapshot found"

    r = _run(base_env, "pdata", "sync", "pull", "--all-projects")
    assert r.returncode == 0, r.stderr
    assert "pulled: alpha" in r.stdout
    assert "skipped (local newer): beta" in r.stdout
    assert "no snapshot found:" in r.stdout


def test_sync_pull_all_projects_force_overwrites_newer_local(base_env, tmp_path):
    import os
    import time

    _run(base_env, "pdata", "add", "--project", "beta", "--group", "notes", "--content", "y")
    _run(base_env, "pdata", "sync", "push", "--project", "beta")
    live_beta = tmp_path / "project-db" / "beta.db"
    os.utime(live_beta, (time.time() + 10, time.time() + 10))

    r = _run(base_env, "pdata", "sync", "pull", "--all-projects", "--force")
    assert r.returncode == 0, r.stderr
    assert "pulled: beta" in r.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ccst_pdata_sync_cli.py -v`
Expected: FAIL — `NameError: name '_cmd_pdata_sync_pull_all' is not defined`

- [ ] **Step 3: Write the implementation**

Add next to `_cmd_pdata_sync_pull_project`:

```python
def _cmd_pdata_sync_pull_all(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import sync

    pulled: list[str] = []
    skipped: list[str] = []
    not_found: list[str] = []
    for project in sync.discover_synced_projects():
        snapshots = sync.list_snapshots(project)
        if not snapshots:
            not_found.append(project)
            continue
        try:
            sync.restore_snapshot(project, snapshots[0], force=args.force)
            pulled.append(project)
        except sync.StaleRestoreError:
            skipped.append(project)
        except RuntimeError as exc:
            print(f"ccst pdata sync pull: {project}: {exc}", file=sys.stderr)
            skipped.append(project)

    print(f"pulled: {', '.join(pulled) if pulled else '(none)'}")
    print(f"skipped (local newer): {', '.join(skipped) if skipped else '(none)'}")
    print(f"no snapshot found: {', '.join(not_found) if not_found else '(none)'}")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ccst_pdata_sync_cli.py -v`
Expected: 10 passed (8 from Task 5 + 2 new)

- [ ] **Step 5: Commit**

```bash
git add src/cc_session_tools/cli/ccst.py tests/test_ccst_pdata_sync_cli.py
git commit -m "feat: add 'ccst pdata sync pull --all-projects'"
```

---

## Verification

- [ ] **Run the full test suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: all tests pass, including every new file in this plan

- [ ] **Run the linter/type-checker if configured**

Run: `uv run ruff check src/ tests/` (and `uv run mypy src/` if configured for this repo — check
`pyproject.toml`/CI config for the exact invocation)
Expected: no new errors introduced by this plan's files

- [ ] **Manual smoke test** (requires quitting any running Claude Code session first, per the
  running-gate design note in the overview doc)

```bash
uv run python -m cc_session_tools.cli.ccst pdata add --project smoketest --group notes --content hello
uv run python -m cc_session_tools.cli.ccst pdata sync push --project smoketest
uv run python -m cc_session_tools.cli.ccst pdata sync pull --project smoketest
```

## Handoff

This plan is independent of Plan 2. Plan 3 (`backup-state`) has one task (its last) that extends
`_cmd_pdata_sync_push`/`_cmd_pdata_sync_pull` added here — land this plan first if doing all three
in sequence.
