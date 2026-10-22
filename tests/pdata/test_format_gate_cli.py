"""The format gate: on a format 3 (integer id) database every record verb refuses, writes nothing
and names `ccst pdata migrate-uuid`; the exempt verbs still run."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from .legacy_fixture import make_format3_db, snapshot_dir

_SOME_ID = "40090682-d73d-4958-a608-9c9960620f31"


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


def test_all_projects_refusal_names_only_the_unmigrated_projects(legacy):
    env, tmp_path = legacy
    r = _run(env, "pdata", "add", "--project", "new", "--group", "notes", "--content", "x")
    assert r.returncode == 0, r.stderr
    r = _run(env, "pdata", "dump", "--all-projects")
    assert r.returncode == 1
    assert "old: database is format 3" in r.stderr
    assert "new:" not in r.stderr


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


def test_sync_check_still_runs_on_a_format_3_database(legacy):
    env, _ = legacy
    r = _run(env, "pdata", "sync-check", "--project", "old")
    assert "database is format 3" not in r.stderr
    assert r.returncode in (0, 1), (r.stdout, r.stderr)
    assert "ccst pdata sync-check: old:" in r.stdout + r.stderr


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
