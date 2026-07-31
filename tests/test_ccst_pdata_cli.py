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


def test_pdata_add_content_only(base_env):
    r = _run(base_env, "pdata", "add", "--project", "testproj",
              "--group", "ccst-ideas", "--content", "an idea")
    assert r.returncode == 0, r.stderr
    assert "1" in r.stdout


def test_pdata_add_rejects_invalid_group(base_env):
    r = _run(base_env, "pdata", "add", "--project", "testproj",
              "--group", "Not Valid", "--content", "an idea")
    assert r.returncode == 2
    assert "record_group" in r.stderr


def test_pdata_add_accepts_created_at_flag(base_env):
    """CLI-level regression test for spec §5's `--created-at <epoch>` flag: confirms argparse
    actually accepts it and forwards it to service.add_record without erroring, not just that
    the flag is reachable via the Python API. (Task 12's `ccst pdata get` later adds an
    end-to-end check that the value is actually persisted.)"""
    r = _run(base_env, "pdata", "add", "--project", "testproj",
              "--group", "ccst-ideas", "--content", "an old idea",
              "--created-at", "1000")
    assert r.returncode == 0, r.stderr
    assert "1" in r.stdout
