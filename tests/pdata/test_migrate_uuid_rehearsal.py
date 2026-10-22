"""End-to-end rehearsal of the format 3 to format 4 migration (task 5.5), through the real `ccst`
CLI: two simulated machines, each with its own HOME, data directory and machine name, share one
project root - and so one `.pdata-db-dump/` directory, as a synced folder would.

1. Both machines hold the same converged format 3 state: machine A's database and the format 3
   dump it published, and machine B's database rebuilt from that dump (what 3.x rehydrate did).
   `sync-check` reports "migration pending" with the format 3 dump "unchanged" on both.
2. Machine A: dry run, `--write`, `verify`, `sync-check` - published the format 4 dump.
3. Machine B: `sync-check` reports the format mismatch; dry run plans to adopt; `--write` adopts.
4. Both hold identical record ids. A adds a record and syncs, B syncs and adds a record and
   syncs, A syncs: no fork at any step, and both end with the same four records.

The same command sequence is recorded for the runbook in the agent folder's
rehearsal-commands.md.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import dump, formats

from .legacy_fixture import make_format3_db, snapshot_dir, write_format3_dump

PROJECT = "household"


def _machine_env(tmp_path: Path, name: str) -> dict[str, str]:
    home = tmp_path / name / "home"
    home.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    pgrep = bin_dir / "pgrep"
    if not pgrep.exists():
        # No `claude` process: keeps the occupancy precondition deterministic when the suite
        # runs inside a live `claude` session.
        pgrep.write_text("#!/bin/sh\nexit 1\n")
        pgrep.chmod(0o755)
    env = {
        key: value for key, value in os.environ.items()
        if key not in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    }
    env.update({
        "HOME": str(home),
        "CCST_DATA_HOME": str(home / ".local" / "share" / "claude"),
        "CCST_PROJECT_DB_DIR": str(tmp_path / name / "project-db"),
        "CCST_PROJECTS_ROOT": str(tmp_path / "shared-projects"),
        "CCST_MACHINE_NAME": name,
        "CCST_HOOKS_DIR": str(tmp_path / name / "hooks"),
        "CC_SCHEDULER_DIR": str(tmp_path / name / "sched"),
        "CCCS_CREDS_PATH": str(tmp_path / "no-creds-here"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    })
    return env


def _ccst(env: dict[str, str], *args: str, expect: int = 0) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True, env=env, cwd=env["HOME"],
    )
    output = result.stdout + result.stderr
    assert result.returncode == expect, f"ccst {' '.join(args)} -> {result.returncode}\n{output}"
    return output


def _db(env: dict[str, str]) -> Path:
    return Path(env["CCST_PROJECT_DB_DIR"]) / f"{PROJECT}.db"


def _records(env: dict[str, str]) -> list[tuple[str, str, int | None]]:
    conn = sqlite3.connect(str(_db(env)))
    try:
        return sorted(
            (str(r[0]), str(r[1]), r[2])
            for r in conn.execute("SELECT id, content, deleted_at FROM records")
        )
    finally:
        conn.close()


@pytest.fixture
def machines(tmp_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Machines A and B on the same converged format 3 state."""
    a = _machine_env(tmp_path, "machine-a")
    b = _machine_env(tmp_path, "machine-b")
    project_root = Path(a["CCST_PROJECTS_ROOT"]) / PROJECT
    project_root.mkdir(parents=True)
    make_format3_db(_db(a))
    write_format3_dump(_db(a), project_root, machine_id="machine-a", vector={"machine-a": 3})
    latest = dump.latest_path(project_root, pdata_format=formats.PDATA_FORMAT_INTEGER_IDS)
    _db(b).parent.mkdir(parents=True)
    conn = sqlite3.connect(str(_db(b)))
    try:
        conn.executescript(dump.sql_body(latest.read_text()))
    finally:
        conn.close()
    return a, b


def test_two_machines_migrate_to_identical_ids_and_then_sync_without_a_fork(
    machines: tuple[dict[str, str], dict[str, str]], tmp_path: Path,
) -> None:
    a, b = machines
    project_root = Path(a["CCST_PROJECTS_ROOT"]) / PROJECT
    format3_dump_dir = dump.dump_dir(project_root, pdata_format=formats.PDATA_FORMAT_INTEGER_IDS)
    format3_files = {
        name: data for name, data in snapshot_dir(format3_dump_dir).items()
        if not name.startswith("v4/")
    }

    # 1. Converged on format 3, everything else refuses or reports pending.
    for env in (a, b):
        out = _ccst(env, "pdata", "sync-check", "--all-projects")
        assert "migration-pending 1" in out
        out = _ccst(env, "pdata", "sync-check", "--project", PROJECT)
        assert "migration pending (format 3 dump: unchanged)" in out
        _ccst(env, "pdata", "list", "--project", PROJECT, "--group", "notes", expect=1)

    # 2. Machine A migrates first.
    out = _ccst(a, "pdata", "migrate-uuid", "--all-projects")
    assert f"{PROJECT}: dry run - nothing was changed" in out
    assert "re-run with --write" in out
    out = _ccst(a, "pdata", "migrate-uuid", "--all-projects", "--write")
    assert f"{PROJECT}: migrated to format 4" in out
    assert "migration-backups/pdata-household-pre-4.0.0-" in out
    _ccst(a, "pdata", "verify", "--all-projects")
    out = _ccst(a, "pdata", "sync-check", "--project", PROJECT)
    assert "unchanged" in out
    out = _ccst(a, "pdata", "migrate-uuid", "--all-projects", "--write")
    assert "already migrated" in out

    # 3. Machine B adopts.
    out = _ccst(b, "pdata", "sync-check", "--project", PROJECT)
    assert "migration pending" in out and "published dump is format 4" in out
    out = _ccst(b, "pdata", "migrate-uuid", "--all-projects")
    assert "adopt the published format 4 dump" in out
    out = _ccst(b, "pdata", "migrate-uuid", "--all-projects", "--write")
    assert f"{PROJECT}: migrated to format 4 by adopting the published format 4 dump" in out
    _ccst(b, "pdata", "verify", "--all-projects")
    out = _ccst(b, "pdata", "sync-check", "--project", PROJECT)
    assert "unchanged" in out

    assert _records(a) == _records(b)
    assert all(formats.database_format(_db(env)) == formats.PDATA_FORMAT_UUID_IDS
               for env in (a, b))

    # 4. Ordinary use afterwards: no fork in either direction.
    _ccst(a, "pdata", "add", "--project", PROJECT, "--group", "notes", "--content", "from a")
    assert "published" in _ccst(a, "pdata", "sync-check", "--project", PROJECT)
    assert "fast-forwarded" in _ccst(b, "pdata", "sync-check", "--project", PROJECT)
    _ccst(b, "pdata", "add", "--project", PROJECT, "--group", "notes", "--content", "from b")
    assert "published" in _ccst(b, "pdata", "sync-check", "--project", PROJECT)
    assert "fast-forwarded" in _ccst(a, "pdata", "sync-check", "--project", PROJECT)

    assert _records(a) == _records(b)
    assert sorted(content for _, content, _ in _records(a)) == [
        "first", "from a", "from b", "second",
    ]
    # The format 3 dump was never touched.
    assert {
        name: data for name, data in snapshot_dir(format3_dump_dir).items()
        if not name.startswith("v4/")
    } == format3_files


def _ccsched(env: dict[str, str], *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", *args],
        capture_output=True, text=True, env=env, cwd=env["HOME"],
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"ccsched {' '.join(args)} -> {result.returncode}\n{output}"
    return output


_PDATA_JOBS = {
    "pdata-sync-hourly": ("ccst", "pdata", "sync-check", "--all-projects"),
    "pm-session-output-reconcile": (
        "ccst", "pdata", "reconcile-session-output", "--all-projects",
    ),
    "pdata-verify-all": ("ccst", "pdata", "verify", "--all-projects"),
}


def test_runbook_job_names_are_the_bundled_pdata_jobs() -> None:
    from cc_session_tools.lib.scheduler import bundled_jobs

    bundled = {job.job_id: job.command for job in bundled_jobs.BUNDLED_CCSCHED_JOBS}
    assert {name: bundled[name] for name in _PDATA_JOBS} == _PDATA_JOBS


def test_runbook_ccsched_and_doctor_steps(
    machines: tuple[dict[str, str], dict[str, str]],
) -> None:
    """The runbook's job and doctor steps: disable the pdata jobs, doctor FAILs before and is OK
    after the migration, re-enable the jobs."""
    a, _ = machines
    for job_id, command in _PDATA_JOBS.items():
        _ccsched(a, "add", "--id", job_id, "--cadence", "daily@03:00", "--command", *command)
    for job_id in _PDATA_JOBS:
        assert f"disabled {job_id}" in _ccsched(a, "disable", job_id)
    listing = _ccsched(a, "list")
    for job_id in _PDATA_JOBS:
        [row] = [line for line in listing.splitlines() if line.startswith(job_id + " ")]
        assert " false " in row

    out = _ccst(a, "doctor", "--all", "--no-pypi", expect=1)
    assert f"[FAIL] migration-to-4.0.0:pdata-uuid:{PROJECT}" in out
    assert formats.RUNBOOK_PATH in out

    _ccst(a, "pdata", "migrate-uuid", "--all-projects", "--write")
    out = _ccst(a, "doctor", "--all", "--no-pypi", expect=1)
    assert f"[OK  ] migration-to-4.0.0:pdata-uuid:{PROJECT}" in out

    for job_id in _PDATA_JOBS:
        assert f"enabled {job_id}" in _ccsched(a, "enable", job_id)


def test_lineage_mismatch_recovery_moves_the_database_aside_and_rehydrates(
    machines: tuple[dict[str, str], dict[str, str]], tmp_path: Path,
) -> None:
    """Machine B made a format 3 write after A migrated: migrate-uuid refuses; the runbook's
    recovery (move the database aside, `ccst pdata rehydrate`) leaves B on A's format 4 data,
    with the old database kept to copy the missing change from."""
    a, b = machines
    _ccst(a, "pdata", "migrate-uuid", "--all-projects", "--write")
    conn = sqlite3.connect(str(_db(b)))
    try:
        conn.execute("UPDATE pdata_meta SET revision = revision + 1")
        conn.execute(
            "INSERT INTO records (id, record_group, content, created_at, updated_at) "
            "VALUES (3, 'notes', 'only on b', 2000, 2000)"
        )
        conn.commit()
    finally:
        conn.close()

    out = _ccst(b, "pdata", "migrate-uuid", "--all-projects", "--write", expect=1)
    assert "[NO] this database is the format 3 state the format 4 dump came from" in out
    assert f"ccst pdata rehydrate --project {PROJECT}" in out

    aside = tmp_path / "aside" / f"{PROJECT}.db"
    aside.parent.mkdir()
    _db(b).rename(aside)
    out = _ccst(b, "pdata", "rehydrate", "--project", PROJECT)
    assert "fast-forwarded" in out
    assert _records(a) == _records(b)
    out = _ccst(b, "pdata", "migrate-uuid", "--all-projects", "--write")
    assert "already migrated" in out
    _ccst(b, "pdata", "verify", "--all-projects")


def test_rollback_restores_the_backup_and_the_format3_dump_still_matches(
    machines: tuple[dict[str, str], dict[str, str]], tmp_path: Path,
) -> None:
    """The runbook's rollback on one machine: move the format 4 database aside and copy the
    named backup back; the restored database is format 3 and converged with the untouched
    format 3 dump, which is what 3.x needs to carry on syncing."""
    import shutil

    a, _ = machines
    before = _records(a)
    out = _ccst(a, "pdata", "migrate-uuid", "--all-projects", "--write")
    [backup_line] = [line for line in out.splitlines() if line.strip().startswith("backup: ")]
    backup = Path(backup_line.split("backup: ", 1)[1].strip())

    aside = tmp_path / "aside"
    aside.mkdir()
    for sidecar in (_db(a), Path(f"{_db(a)}-wal"), Path(f"{_db(a)}-shm")):
        if sidecar.exists():
            sidecar.rename(aside / sidecar.name)
    shutil.copy2(backup, _db(a))

    assert formats.database_format(_db(a)) == formats.PDATA_FORMAT_INTEGER_IDS
    assert _records(a) == before
    out = _ccst(a, "pdata", "sync-check", "--project", PROJECT)
    assert "migration pending (format 3 dump: unchanged)" in out
