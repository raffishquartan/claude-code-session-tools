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


def test_apply_registers_every_bundled_job(base_env):
    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    for job_id in (
        "ccst-doctor-drift-weekly",
        "update-command-cache-reminder",
        "telemetry-trim-weekly",
        "ccsched-no-op-demoing-job-visibility",
        "clean-hook-sessions-weekly",
    ):
        assert f"registered: {job_id}" in r.stdout


def test_disabled_job_is_reported_not_touched(base_env):
    _run(base_env, "ccsched-jobs", "install", "--apply")
    r = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", "disable", "telemetry-trim-weekly"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=base_env,
    )
    assert r.returncode == 0, r.stderr

    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    assert "disabled (not touched): telemetry-trim-weekly" in r.stdout
    assert "already registered: telemetry-trim-weekly" not in r.stdout


def test_changed_job_is_reported_not_touched_and_not_overwritten(base_env):
    _run(base_env, "ccsched-jobs", "install", "--apply")
    r = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", "edit",
         "telemetry-trim-weekly", "--timeout", "9s"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=base_env,
    )
    assert r.returncode == 0, r.stderr

    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    assert "changed (not touched): telemetry-trim-weekly - timeout" in r.stdout
    assert "already registered: telemetry-trim-weekly" not in r.stdout

    show = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", "show", "telemetry-trim-weekly"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=base_env,
    )
    assert re.search(r"timeout:\s+9s", show.stdout)


def _remove(env: dict, job_id: str) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", "remove", job_id],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=env,
    )
    assert r.returncode == 0, r.stderr


def test_a_removed_bundled_job_is_reported_deleted_not_re_added(base_env):
    _run(base_env, "ccsched-jobs", "install", "--apply")
    _remove(base_env, "telemetry-trim-weekly")

    r = _run(base_env, "ccsched-jobs", "install")
    assert r.returncode == 0, r.stderr
    assert "deleted (not re-added): telemetry-trim-weekly" in r.stdout
    assert "would register: telemetry-trim-weekly" not in r.stdout

    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    assert "deleted (not re-added): telemetry-trim-weekly" in r.stdout
    assert "registered: telemetry-trim-weekly" not in r.stdout
    show = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", "show", "telemetry-trim-weekly"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=base_env,
    )
    assert show.returncode == 2  # still gone - never silently re-added


def test_reinstall_brings_a_deleted_job_back(base_env):
    _run(base_env, "ccsched-jobs", "install", "--apply")
    _remove(base_env, "telemetry-trim-weekly")

    r = _run(base_env, "ccsched-jobs", "install", "--apply", "--reinstall", "telemetry-trim-weekly")
    assert r.returncode == 0, r.stderr
    assert "reinstalled: telemetry-trim-weekly" in r.stdout
    show = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", "show", "telemetry-trim-weekly"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=base_env,
    )
    assert show.returncode == 0, show.stderr


def test_a_never_installed_job_is_not_reported_as_deleted(base_env):
    r = _run(base_env, "ccsched-jobs", "install")
    assert r.returncode == 0, r.stderr
    assert "would register: telemetry-trim-weekly" in r.stdout
    assert "deleted" not in r.stdout
