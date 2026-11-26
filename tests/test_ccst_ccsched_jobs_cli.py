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
        "session-gc-report-weekly",
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
    assert (
        "changed (not touched): telemetry-trim-weekly - 1 field differs "
        "from the bundled definition:" in r.stdout
    )
    assert "timeout:" in r.stdout
    assert "- bundled: 60s" in r.stdout
    assert "+ current: 9s" in r.stdout
    assert "run 'ccsched edit telemetry-trim-weekly' to realign" in r.stdout
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


# ---------- self-improvement jobs and a bash-script registration of the same id ----------


def _ccsched(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", *args],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=env,
    )


def _add_bash_version(env: dict, job_id: str, script: str) -> None:
    r = _ccsched(
        env, "add", "--id", job_id, "--cadence", "every:7d", "--coalesce", "one",
        "--catchup-window", "14d", "--timeout", "60s", "--surface", "--command",
        "bash", f"/example/repos/config/scripts/self-improvement/{script}",
    )
    assert r.returncode == 0, r.stderr


def test_apply_registers_both_self_improvement_jobs_enabled(base_env):
    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    for job_id, verb in (
        ("self-improvement-signals", "collect-signals"),
        ("self-improvement-auto-review", "auto-review"),
    ):
        assert f"registered: {job_id}" in r.stdout
        show = _ccsched(base_env, "show", job_id)
        assert re.search(rf"command:\s+ccst self-improvement {verb}$", show.stdout, re.M)
        assert re.search(r"enabled:\s+true", show.stdout)


def test_a_bash_script_registration_is_reported_and_kept(base_env):
    _add_bash_version(base_env, "self-improvement-signals", "collect-signals.sh")
    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    assert (
        "conflicting definition (not touched): self-improvement-signals - runs "
        "collect-signals.sh, which the bundled job replaces; to switch to the bundled job run "
        "'ccsched remove self-improvement-signals' then 'ccst ccsched-jobs install --apply'"
    ) in r.stdout
    assert "changed (not touched): self-improvement-signals" not in r.stdout
    show = _ccsched(base_env, "show", "self-improvement-signals")
    assert "collect-signals.sh" in show.stdout


def test_removing_a_bash_script_registration_lets_install_register_the_bundled_job(base_env):
    _add_bash_version(base_env, "self-improvement-auto-review", "run-headless-review.sh")
    _run(base_env, "ccsched-jobs", "install", "--apply")
    _remove(base_env, "self-improvement-auto-review")
    r = _run(base_env, "ccsched-jobs", "install", "--apply")
    assert r.returncode == 0, r.stderr
    assert "registered: self-improvement-auto-review" in r.stdout
    assert "deleted (not re-added): self-improvement-auto-review" not in r.stdout
    show = _ccsched(base_env, "show", "self-improvement-auto-review")
    assert re.search(r"command:\s+ccst self-improvement auto-review$", show.stdout, re.M)


def test_dry_run_reports_a_bash_script_registration_too(base_env):
    _add_bash_version(base_env, "self-improvement-signals", "collect-signals.sh")
    r = _run(base_env, "ccsched-jobs", "install")
    assert "conflicting definition (not touched): self-improvement-signals" in r.stdout
