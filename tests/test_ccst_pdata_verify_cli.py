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
    env["CCST_PROJECTS_ROOT"] = str(tmp_path / "projects")
    return env


def test_pdata_verify_clean_project_exits_zero(base_env):
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "ccst-ideas",
         "--content", "an idea")
    r = _run(base_env, "pdata", "verify", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "demo: OK" in r.stdout


def test_pdata_verify_reports_issue_and_exits_one(base_env):
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "filings",
         "--content", "x", "--file", "missing.pdf")
    r = _run(base_env, "pdata", "verify", "--project", "demo")
    assert r.returncode == 1
    assert "FAIL" in r.stdout


def test_pdata_verify_all_with_no_projects_exits_two(base_env):
    r = _run(base_env, "pdata", "verify", "--all-projects")
    assert r.returncode == 2
    assert "no project" in r.stderr.lower()


def test_pdata_verify_all_default_is_one_line_summary_when_clean(base_env):
    """§ compact-summary fix: the scheduled pdata-verify-all job's default
    output was "very long" (one line per project) even when every project is
    clean - --all-projects without --verbose must collapse that to a single
    confirming line, not print each project."""
    _run(base_env, "pdata", "add", "--project", "alpha", "--group", "notes",
         "--content", "x")
    _run(base_env, "pdata", "add", "--project", "beta", "--group", "notes",
         "--content", "y")
    r = _run(base_env, "pdata", "verify", "--all-projects")
    assert r.returncode == 0, r.stderr
    assert "alpha: OK" not in r.stdout
    assert "beta: OK" not in r.stdout
    assert r.stdout.strip().count("\n") == 0  # exactly one line
    assert "OK" in r.stdout
    assert "2" in r.stdout  # project count


def test_pdata_verify_all_verbose_shows_full_per_project_listing(base_env):
    _run(base_env, "pdata", "add", "--project", "alpha", "--group", "notes",
         "--content", "x")
    _run(base_env, "pdata", "add", "--project", "beta", "--group", "notes",
         "--content", "y")
    r = _run(base_env, "pdata", "verify", "--all-projects", "--verbose")
    assert r.returncode == 0, r.stderr
    assert "alpha: OK" in r.stdout
    assert "beta: OK" in r.stdout


def test_pdata_verify_all_default_flags_issues_and_suggests_verbose(base_env):
    _run(base_env, "pdata", "add", "--project", "alpha", "--group", "notes",
         "--content", "x")
    _run(base_env, "pdata", "add", "--project", "broken", "--group", "filings",
         "--content", "x", "--file", "missing.pdf")
    r = _run(base_env, "pdata", "verify", "--all-projects")
    assert r.returncode == 1
    assert "broken: OK" not in r.stdout
    assert "--verbose" in r.stdout
    assert "1" in r.stdout  # 1 of 2 projects has issues


def test_pdata_verify_single_project_is_unaffected_by_verbose_split(base_env):
    """--project is already just one project's worth of output - the compact/
    verbose split only applies to --all-projects."""
    _run(base_env, "pdata", "add", "--project", "demo", "--group", "ccst-ideas",
         "--content", "an idea")
    r = _run(base_env, "pdata", "verify", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "demo: OK" in r.stdout


def test_pdata_verify_requires_project_or_all(base_env):
    r = _run(base_env, "pdata", "verify")
    assert r.returncode == 2


def test_pdata_verify_project_not_found_exits_two(base_env):
    """A --project name with no existing .db (typo, or genuinely never touched) must be
    reported as an error, never silently created and reported clean (run_verify()'s own
    ValueError, plan Decision 8's "2 for a CLI/validation error")."""
    r = _run(base_env, "pdata", "verify", "--project", "never-touched-project")
    assert r.returncode == 2
    assert "no data store" in r.stderr.lower()
