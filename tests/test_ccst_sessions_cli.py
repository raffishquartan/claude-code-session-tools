"""Tests for `ccst sessions migrate` and `ccst sessions list`."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent.parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path, monkeypatch):
    import os
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(tmp_path / "repos")
    (tmp_path / "repos").mkdir()
    env["CCST_SESSIONS_DIR"] = str(tmp_path / "db")
    return env


def test_sessions_list_empty_db(base_env):
    r = _run(base_env, "sessions", "list")
    assert r.returncode == 0
    assert "No sessions recorded" in r.stdout


def test_sessions_migrate_dry_run_no_sources(base_env):
    r = _run(base_env, "sessions", "migrate", "--dry-run")
    assert r.returncode == 0
    assert "dry-run mode" in r.stdout


def test_sessions_migrate_then_list_shows_row(base_env, tmp_path):
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    (tags_dir / "uuid-1.tag").write_text("my-feature\n")
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    sess = proj / "cc-sessions" / "20260713-my-feature"
    (sess / "working").mkdir(parents=True)

    r_migrate = _run(base_env, "sessions", "migrate", "--tags-dir", str(tags_dir))
    assert r_migrate.returncode == 0

    r_list = _run(base_env, "sessions", "list")
    assert r_list.returncode == 0
    assert "20260713-my-feature" in r_list.stdout


def test_sessions_list_json_output(base_env, tmp_path):
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    (proj / "cc-sessions" / "20260713-json-test" / "working").mkdir(parents=True)
    _run(base_env, "sessions", "migrate")

    r = _run(base_env, "sessions", "list", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert any(row["basename"] == "20260713-json-test" for row in data)


def test_tags_noun_no_longer_exists(base_env):
    """D4: `ccst tags migrate` is retired."""
    r = _run(base_env, "tags", "migrate")
    assert r.returncode != 0
