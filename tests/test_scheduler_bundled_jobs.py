from __future__ import annotations

from pathlib import Path

from cc_session_tools.lib.scheduler import bundled_jobs
from cc_session_tools.lib.scheduler.bundled_jobs import diff_from_bundled
from cc_session_tools.lib.scheduler.jobspec import validate_job_fields


def test_bundled_jobs_contains_session_output_reconcile_job():
    ids = [job.job_id for job in bundled_jobs.BUNDLED_CCSCHED_JOBS]
    assert "pm-session-output-reconcile" in ids


def test_session_output_job_command_matches_the_reconcile_cli():
    job = next(
        j for j in bundled_jobs.BUNDLED_CCSCHED_JOBS
        if j.job_id == "pm-session-output-reconcile"
    )
    assert job.command == ("ccst", "pdata", "reconcile-session-output", "--all-projects")
    assert job.cadence == "every:7d"
    assert job.coalesce == "one"


def test_bundled_job_ids_are_unique():
    ids = [job.job_id for job in bundled_jobs.BUNDLED_CCSCHED_JOBS]
    assert len(ids) == len(set(ids))


def test_bundled_jobs_contains_pdata_verify_all_job():
    ids = [job.job_id for job in bundled_jobs.BUNDLED_CCSCHED_JOBS]
    assert "pdata-verify-all" in ids


def test_pdata_verify_all_job_command_and_cadence():
    job = next(
        j for j in bundled_jobs.BUNDLED_CCSCHED_JOBS if j.job_id == "pdata-verify-all"
    )
    assert job.command == ("ccst", "pdata", "verify", "--all-projects")
    assert job.cadence == "daily@03:00"  # avoids interactive-session hours
    assert job.coalesce == "one"
    assert job.surface is False  # results reach ccst doctor, not a direct interrupt (spec §8.2)


def test_pdata_verify_all_job_treats_no_projects_yet_as_success():
    """Exit 2 means "zero project .db files found" (plan Decision 8) — expected on any machine
    that hasn't adopted pdata yet, not a crash. A real per-project issue exits 1, which must
    still count as a failure, so only 2 is added alongside the default 0."""
    job = next(
        j for j in bundled_jobs.BUNDLED_CCSCHED_JOBS if j.job_id == "pdata-verify-all"
    )
    assert job.success_exit_codes == (0, 2)


def test_session_output_job_uses_default_success_exit_codes():
    job = next(
        j for j in bundled_jobs.BUNDLED_CCSCHED_JOBS
        if j.job_id == "pm-session-output-reconcile"
    )
    assert job.success_exit_codes == (0,)


def _job(job_id: str) -> bundled_jobs.BundledJob:
    return next(j for j in bundled_jobs.BUNDLED_CCSCHED_JOBS if j.job_id == job_id)


def test_bundled_jobs_contains_the_2026_08_batch():
    ids = {job.job_id for job in bundled_jobs.BUNDLED_CCSCHED_JOBS}
    assert ids >= {
        "ccst-doctor-drift-weekly",
        "update-command-cache-reminder",
        "telemetry-trim-weekly",
        "ccsched-no-op-demoing-job-visibility",
        "clean-hook-sessions-weekly",
    }


def test_clean_hook_sessions_weekly_job_command_points_at_the_bundled_skill_script():
    """The path is resolved against Path.home() at import time, never a literal - it must
    match wherever `ccst skills install` symlinks the clean-hook-sessions skill on THIS
    machine, not any one hardcoded machine's home directory."""
    job = _job("clean-hook-sessions-weekly")
    assert job.command[0] == "python3"
    expected_script = str(
        Path.home() / ".claude" / "skills" / "clean-hook-sessions" / "scripts"
        / "clean-hook-sessions.py"
    )
    assert job.command[1] == expected_script
    assert job.command[2:] == ("--older-than", "28", "--keep-n", "50", "--execute")


def test_doctor_drift_job_treats_found_drift_as_success():
    """`ccst doctor --drift` exits 1 when it finds un-muted drift to report - that's the job
    doing its job, not a crash, so it must not count toward auto-suspend."""
    job = _job("ccst-doctor-drift-weekly")
    assert job.command == ("ccst", "doctor", "--drift")
    assert job.success_exit_codes == (0, 1)


def test_update_command_cache_reminder_job_is_a_plain_echo():
    job = _job("update-command-cache-reminder")
    assert job.command[0] == "echo"
    assert job.cadence == "every:2w"


def test_telemetry_trim_weekly_job_command():
    job = _job("telemetry-trim-weekly")
    assert job.command == ("ccst", "telemetry", "trim", "--max-size", "10", "--max-age-days", "90")


def _spec_for(job: bundled_jobs.BundledJob, **overrides):
    fields = dict(
        job_id=job.job_id, cadence=job.cadence, coalesce=job.coalesce,
        command=list(job.command), surface=job.surface, enabled=True,
        catchup_window=job.catchup_window, timeout=job.timeout,
        success_exit_codes=job.success_exit_codes,
    )
    fields.update(overrides)
    return validate_job_fields(**fields)


def test_diff_from_bundled_is_empty_for_an_untouched_job():
    job = _job("pdata-verify-all")
    assert diff_from_bundled(_spec_for(job), job) == ()


def test_diff_from_bundled_ignores_enabled_state():
    """enabled is per-machine operational state a human toggles via ccsched enable/disable, not
    part of a job's bundled definition, so a disabled job with everything else untouched is not
    'changed'."""
    job = _job("pdata-verify-all")
    assert diff_from_bundled(_spec_for(job, enabled=False), job) == ()


def test_diff_from_bundled_names_every_differing_field():
    job = _job("pdata-verify-all")
    spec = _spec_for(job, cadence="every:3d", timeout="30s")
    assert diff_from_bundled(spec, job) == ("cadence", "timeout")


def test_diff_from_bundled_detects_a_changed_command():
    job = _job("pdata-verify-all")
    spec = _spec_for(job, command=["ccst", "pdata", "verify", "--project", "x"])
    assert diff_from_bundled(spec, job) == ("command",)
