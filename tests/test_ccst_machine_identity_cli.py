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
    env["CCST_DATA_HOME"] = str(tmp_path / "data-home")
    env.pop("CCST_MACHINE_NAME", None)
    return env


def test_show_reports_unconfirmed_hostname_fallback(base_env):
    r = _run(base_env, "machine-identity", "show")
    assert r.returncode == 0
    assert "(unconfirmed" in r.stdout
    assert "ccst machine-identity confirm --name <name>" in r.stdout


def test_confirm_requires_name(base_env):
    r = _run(base_env, "machine-identity", "confirm")
    assert r.returncode == 2


def test_confirm_rejects_an_empty_name(base_env):
    r = _run(base_env, "machine-identity", "confirm", "--name", "")
    assert r.returncode == 2
    assert "ccst machine-identity:" in r.stderr


def test_confirm_rejects_a_whitespace_only_name(base_env):
    r = _run(base_env, "machine-identity", "confirm", "--name", "   ")
    assert r.returncode == 2
    assert "ccst machine-identity:" in r.stderr


def test_confirm_then_show_reports_confirmed_name(base_env):
    r = _run(base_env, "machine-identity", "confirm", "--name", "ltxy")
    assert r.returncode == 0
    assert "ltxy" in r.stdout

    r = _run(base_env, "machine-identity", "show")
    assert r.returncode == 0
    assert r.stdout.strip() == "ltxy (confirmed)"


def test_env_override_beats_stored_confirmation(base_env):
    _run(base_env, "machine-identity", "confirm", "--name", "ltxy")

    base_env["CCST_MACHINE_NAME"] = "other-box"
    r = _run(base_env, "machine-identity", "show")
    assert r.returncode == 0
    assert r.stdout.strip() == "other-box (confirmed)"
