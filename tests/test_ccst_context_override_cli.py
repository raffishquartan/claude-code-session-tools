"""Tests for `ccst context-override [on|off|status]`."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "context-override", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent.parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path, monkeypatch):
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(tmp_path / "repos")
    (tmp_path / "repos").mkdir()
    env["CCST_SESSIONS_DIR"] = str(tmp_path / "db")
    env["CLAUDE_CODE_SESSION_ID"] = "s1"
    return env


def test_on_default_action(base_env):
    result = _run(base_env)
    assert result.returncode == 0
    assert "ON" in result.stdout


def test_off_action(base_env):
    _run(base_env, "on")
    result = _run(base_env, "off")
    assert result.returncode == 0
    assert "OFF" in result.stdout


def test_status_action(base_env):
    result = _run(base_env, "status")
    assert result.returncode == 0
    assert "OFF" in result.stdout  # default, nothing set yet


def test_missing_session_id_errors(base_env):
    base_env.pop("CLAUDE_CODE_SESSION_ID", None)
    result = _run(base_env, "on")
    assert result.returncode == 1
    assert "CLAUDE_CODE_SESSION_ID" in result.stderr


def test_unknown_action_errors(base_env):
    """The subparser's choices=["on", "off", "status"] rejects this before
    _cmd_context_override ever runs - this documents that, not a branch in
    the handler (there isn't one; argparse owns this rejection entirely)."""
    result = _run(base_env, "bogus")
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
