from __future__ import annotations

import json
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


def test_pdata_init_dry_run_empty_project(base_env, tmp_path):
    (tmp_path / "projects" / "biz").mkdir(parents=True)
    r = _run(base_env, "pdata", "init", "--project", "biz")
    assert r.returncode == 0, r.stderr
    assert "no files found" in r.stdout


def test_pdata_init_dry_run_classifies_and_writes_proposal(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "CLAUDE.md").write_text("# demo\n")
    (project_dir / "ideas.csv").write_text("idea,priority\nfirst,1\n")

    r = _run(base_env, "pdata", "init", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "[folder-owned] CLAUDE.md" in r.stdout
    assert "group=ideas strategy=csv-rows" in r.stdout
    assert (project_dir / ".ccst-pdata-proposal.json").exists()


def test_pdata_init_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "init", "--project", "../escape")
    assert r.returncode == 2
    assert "project" in r.stderr


def test_pdata_init_rejects_malformed_proposal_file(base_env, tmp_path):
    """A hand-edited .ccst-pdata-proposal.json missing a required key must produce
    the documented exit-2 validation error, not an uncaught KeyError traceback."""
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / ".ccst-pdata-proposal.json").write_text(
        json.dumps({"entries": [{"path": "x", "classification": "folder-owned"}]})
    )  # missing the required "project" key

    r = _run(base_env, "pdata", "init", "--project", "demo")
    assert r.returncode == 2
    assert "malformed manifest" in r.stderr
