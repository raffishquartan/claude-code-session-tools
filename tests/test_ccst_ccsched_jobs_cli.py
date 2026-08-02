from __future__ import annotations

import os
import re
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
    return env


def test_dry_run_reports_would_register(base_env):
    r = _run(base_env, "ccsched-jobs", "install")
    assert r.returncode == 0, r.stderr
    assert "would register: pm-session-output-reconcile" in r.stdout


def test_apply_registers_the_job(base_env):
    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    assert "registered: pm-session-output-reconcile" in r.stdout


def test_apply_is_idempotent_on_rerun(base_env):
    _run(base_env, "ccsched-jobs", "install", "--apply")
    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    assert "already registered: pm-session-output-reconcile" in r.stdout


def test_apply_registers_pdata_verify_all_with_success_exit_codes(base_env):
    _run(base_env, "ccsched-jobs", "install", "--apply")
    r = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", "show", "pdata-verify-all"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=base_env,
    )
    assert r.returncode == 0, r.stderr
    assert re.search(r"success_exit_codes:\s+0,2", r.stdout)
