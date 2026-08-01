from __future__ import annotations

import os
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


@pytest.fixture
def base_env(tmp_path):
    env = os.environ.copy()
    env["CCST_PROJECT_DB_DIR"] = str(tmp_path / "project-db")
    return env


def _touch(path: Path, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (mtime, mtime))


def test_reconcile_requires_project_or_all_projects(base_env):
    r = _run(base_env, "pdata", "reconcile-session-output")
    assert r.returncode == 2


def test_reconcile_rejects_both_project_and_all_projects(base_env, tmp_path):
    r = _run(
        base_env, "pdata", "reconcile-session-output",
        "--project", "x", "--all-projects",
    )
    assert r.returncode == 2


def test_reconcile_single_project_reports_scanned_and_registered(base_env, tmp_path):
    repo_root = tmp_path / "repos"
    _touch(repo_root / "myproj" / "cc-sessions" / "20260710-a" / "out" / "r.md", 2000)
    base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repo_root)

    r = _run(base_env, "pdata", "reconcile-session-output", "--project", "myproj")

    assert r.returncode == 0, r.stderr
    assert "myproj" in r.stdout
    assert "registered 1" in r.stdout


def test_reconcile_unknown_project_errors(base_env, tmp_path):
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repo_root)

    r = _run(base_env, "pdata", "reconcile-session-output", "--project", "nope")

    assert r.returncode == 1
    assert "nope" in r.stderr


def test_reconcile_all_projects(base_env, tmp_path):
    repo_root = tmp_path / "repos"
    _touch(repo_root / "a" / "cc-sessions" / "20260710-x" / "out" / "r.md", 2000)
    _touch(repo_root / "b" / "cc-sessions" / "20260710-y" / "out" / "r.md", 2000)
    base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repo_root)

    r = _run(base_env, "pdata", "reconcile-session-output", "--all-projects")

    assert r.returncode == 0, r.stderr
    assert "a:" in r.stdout
    assert "b:" in r.stdout


def test_reconcile_no_roots_configured_errors(base_env):
    for var in ("CLAUDE_SESSION_TOOLS_REPO_ROOT", "CLAUDE_SESSION_TOOLS_PROJ_ROOT"):
        base_env.pop(var, None)

    r = _run(base_env, "pdata", "reconcile-session-output", "--all-projects")

    assert r.returncode == 1
    assert "CST-ROOTS-CONFIG-ERROR" in r.stderr


def test_reconcile_dry_run(base_env, tmp_path):
    repo_root = tmp_path / "repos"
    _touch(repo_root / "myproj" / "cc-sessions" / "20260710-a" / "out" / "r.md", 2000)
    base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repo_root)

    r = _run(
        base_env, "pdata", "reconcile-session-output",
        "--project", "myproj", "--dry-run",
    )

    assert r.returncode == 0, r.stderr
    assert "dry-run" in r.stdout


def test_reconcile_schema_only_bootstraps_schema_without_scanning(base_env, tmp_path):
    repo_root = tmp_path / "repos"
    _touch(repo_root / "myproj" / "cc-sessions" / "20260710-a" / "out" / "r.md", 2000)
    base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repo_root)

    r = _run(
        base_env, "pdata", "reconcile-session-output",
        "--project", "myproj", "--schema-only",
    )

    assert r.returncode == 0, r.stderr
    assert "schema ensured" in r.stdout

    # The file was NOT scanned/registered by --schema-only. `pdata query` renders an empty
    # table result as the literal "No rows." (formatting.render), not as empty output.
    r2 = _run(base_env, "pdata", "query", "--project", "myproj", "--group", "session-output")
    assert r2.returncode == 0, r2.stderr
    assert r2.stdout.strip() == "No rows."


def test_reconcile_schema_only_rejects_all_projects(base_env):
    r = _run(
        base_env, "pdata", "reconcile-session-output",
        "--all-projects", "--schema-only",
    )
    assert r.returncode == 2
