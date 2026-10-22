"""The format gate: on a format 3 (integer id) database every record verb refuses, writes nothing
and names `ccst pdata migrate-uuid`; the exempt verbs still run."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from .legacy_fixture import make_format3_db, snapshot_dir

_SOME_ID = "3f2b8c1e-6d4a-4e9b-9a7c-2d5e8f1b0c64"


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=env,
    )


@pytest.fixture
def legacy(tmp_path):
    db_dir = tmp_path / "project-db"
    make_format3_db(db_dir / "old.db")
    projects_root = tmp_path / "projects"
    (projects_root / "old" / "cc-sessions").mkdir(parents=True)
    (projects_root / "old" / "docs").mkdir()
    env = os.environ.copy()
    env["CCST_PROJECT_DB_DIR"] = str(db_dir)
    env["CCST_PROJECTS_ROOT"] = str(projects_root)
    env["CLAUDE_SESSION_TOOLS_PROJ_ROOT"] = str(projects_root)
    return env, tmp_path


_GATED = [
    ("add", "--project", "old", "--group", "notes", "--content", "x"),
    ("schema", "add-field", "--project", "old", "--group", "notes", "--field", "f:TEXT"),
    ("schema", "list", "--project", "old"),
    ("schema", "show", "--project", "old", "--group", "notes"),
    ("get", "--project", "old", "--id", _SOME_ID),
    ("list", "--project", "old", "--group", "notes"),
    ("query", "--project", "old", "--group", "notes"),
    ("update", "--project", "old", "--id", _SOME_ID, "--version", "1", "--content", "x"),
    ("delete", "--project", "old", "--id", _SOME_ID, "--version", "1"),
    ("restore", "--project", "old", "--id", _SOME_ID),
    ("init", "--project", "old"),
    ("init", "--project", "old", "--write"),
    ("reorganize", "--project", "old", "--folder", "docs", "--strategy", "by-year"),
    ("rename-group", "--project", "old", "--from", "notes", "--to", "memos"),
    ("reconcile-session-output", "--project", "old"),
    ("reconcile-session-output", "--all-projects"),
    ("dump", "--project", "old"),
    ("dump", "--all-projects"),
    ("rehydrate", "--project", "old"),
    ("resolve", "--project", "old"),
    ("resolve", "--project", "old", "--choice", f"{_SOME_ID}=local"),
]


@pytest.mark.parametrize("argv", _GATED, ids=lambda a: " ".join(a[:2]))
def test_record_verbs_refuse_on_a_format_3_database_and_write_nothing(legacy, argv):
    env, tmp_path = legacy
    before = snapshot_dir(tmp_path)
    r = _run(env, "pdata", *argv)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "old: database is format 3" in r.stderr
    assert "ccst pdata migrate-uuid --project old" in r.stderr
    assert snapshot_dir(tmp_path) == before


def _add_new_project(env: dict[str, str]) -> None:
    r = _run(env, "pdata", "add", "--project", "new", "--group", "notes", "--content", "x")
    assert r.returncode == 0, r.stderr


def test_dump_all_projects_skips_unmigrated_projects_and_publishes_the_rest(legacy):
    env, tmp_path = legacy
    _add_new_project(env)
    old_before = snapshot_dir(tmp_path / "projects" / "old")

    r = _run(env, "pdata", "dump", "--all-projects")

    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "ccst pdata dump: skipped old: database is format 3" in r.stderr
    assert "new:" not in r.stderr
    assert "published 1 of 1 project(s)" in r.stdout
    assert (tmp_path / "projects" / "new" / ".pdata-db-dump" / "v4" / "latest.sql").exists()
    assert snapshot_dir(tmp_path / "projects" / "old") == old_before


def test_reconcile_all_projects_skips_unmigrated_projects_and_reconciles_the_rest(legacy):
    env, tmp_path = legacy
    _add_new_project(env)
    out = tmp_path / "projects" / "new" / "cc-sessions" / "20261020-x" / "out"
    out.mkdir(parents=True)
    (out / "report.md").write_text("x")

    r = _run(env, "pdata", "reconcile-session-output", "--all-projects")

    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "skipped old: database is format 3" in r.stderr
    assert "new: scanned 1, registered 1" in r.stdout
    assert "old:" not in r.stdout
    r = _run(env, "pdata", "list", "--project", "new", "--group", "session-output")
    assert r.returncode == 0, r.stderr
    assert "report.md" in r.stdout


def test_rehydrate_all_projects_skips_unmigrated_projects_and_checks_the_rest(legacy):
    env, _ = legacy
    _add_new_project(env)
    r = _run(env, "pdata", "rehydrate", "--all-projects")
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "skipped old: database is format 3" in r.stderr
    assert "of 1 project(s)" in r.stdout


def test_all_projects_with_nothing_unmigrated_exits_0(legacy):
    env, tmp_path = legacy
    _add_new_project(env)
    (tmp_path / "project-db" / "old.db").rename(tmp_path / "old.db.aside")
    r = _run(env, "pdata", "dump", "--all-projects")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "skipped" not in r.stderr


def test_a_format_4_project_is_not_gated(legacy):
    env, _ = legacy
    r = _run(env, "pdata", "add", "--project", "new", "--group", "notes", "--content", "x")
    assert r.returncode == 0, r.stderr
    r = _run(env, "pdata", "list", "--project", "new", "--group", "notes")
    assert r.returncode == 0, r.stderr


def test_verify_still_runs_on_a_format_3_database(legacy):
    env, _ = legacy
    r = _run(env, "pdata", "verify", "--project", "old", "--full")
    assert "database is format 3" not in r.stderr
    assert r.stdout.startswith("old: "), (r.stdout, r.stderr)


def test_sync_check_on_a_format_3_database_reports_status_only(legacy):
    env, tmp_path = legacy
    before = snapshot_dir(tmp_path)
    r = _run(env, "pdata", "sync-check", "--project", "old")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert r.stderr == ""
    assert r.stdout.startswith(
        "ccst pdata sync-check: old: migration pending (format 3 dump: not_published) - "
    ), r.stdout
    assert "ccst pdata migrate-uuid --project old" in r.stdout
    assert snapshot_dir(tmp_path) == before


def test_sync_check_all_projects_counts_pending_migrations(legacy):
    env, tmp_path = legacy
    before = snapshot_dir(tmp_path)
    r = _run(env, "pdata", "sync-check", "--all-projects")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "migration-pending 1" in r.stdout
    assert snapshot_dir(tmp_path) == before


def test_readiness_scan_is_not_gated(legacy):
    env, _ = legacy
    r = _run(env, "pdata", "readiness-scan", "--project", "old")
    assert "database is format 3" not in r.stderr
    assert r.returncode == 0, r.stderr


def test_doctor_is_not_gated(legacy):
    env, tmp_path = legacy
    env = {**env, "HOME": str(tmp_path / "home")}
    r = _run(env, "doctor")
    assert "database is format 3" not in r.stderr


def test_rehydrate_reports_a_format_mismatched_dump_as_a_pending_migration(legacy):
    """A format 4 project whose v4 location holds a format 3 dump: rehydrate loads nothing and
    says so, rather than calling it lock contention or a fork."""
    from .legacy_fixture import write_format3_dump

    env, tmp_path = legacy
    r = _run(env, "pdata", "add", "--project", "new", "--group", "notes", "--content", "x")
    assert r.returncode == 0, r.stderr
    project_root = tmp_path / "projects" / "new"
    legacy_dir = write_format3_dump(
        tmp_path / "project-db" / "old.db", project_root, machine_id="machine-a",
        vector={"machine-a": 3},
    )
    v4 = legacy_dir / "v4"
    v4.mkdir()
    for name in ("latest.sql", "latest.sha256"):
        (v4 / name).write_bytes((legacy_dir / name).read_bytes())
    def files() -> dict[str, bytes]:
        # A read-only open of a WAL database may create its -wal/-shm sidecars; data is what
        # must not change.
        return {
            k: v for k, v in snapshot_dir(tmp_path).items() if not k.endswith(("-wal", "-shm"))
        }

    before = files()

    for argv in (("--project", "new"), ("--project", "new", "--force")):
        r = _run(env, "pdata", "rehydrate", *argv)
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert r.stdout.startswith("ccst pdata rehydrate: new: migration pending - "), r.stdout
        assert "published dump is format 3" in r.stdout
        assert "ccst pdata migrate-uuid --project new" in r.stdout
        assert files() == before


def test_a_database_of_an_unknown_format_is_refused(legacy):
    import sqlite3

    env, tmp_path = legacy
    conn = sqlite3.connect(str(tmp_path / "project-db" / "weird.db"))
    try:
        conn.execute("CREATE TABLE records (id BLOB PRIMARY KEY, record_group TEXT)")
        conn.commit()
    finally:
        conn.close()
    before = snapshot_dir(tmp_path)
    r = _run(env, "pdata", "list", "--project", "weird", "--group", "notes")
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "ccst pdata list: weird: records.id is declared 'BLOB'" in r.stderr
    assert snapshot_dir(tmp_path) == before
