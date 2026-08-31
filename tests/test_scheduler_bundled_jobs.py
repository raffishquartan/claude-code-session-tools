from __future__ import annotations

from pathlib import Path

from cc_session_tools.lib.scheduler import bundled_jobs
from cc_session_tools.lib.scheduler.bundled_jobs import (
    diff_from_bundled, diff_from_bundled_detail, render_field_diffs,
)
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
    assert job.surface is True  # also shows the launch notice, not just the eventual result


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
        "session-gc-report-weekly",
    }


def test_session_gc_report_weekly_job_command():
    job = _job("session-gc-report-weekly")
    assert job.command == ("ccst", "gc", "report")
    assert job.cadence == "every:7d"


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
    assert job.command == ("ccst", "telemetry", "trim", "--max-size", "50", "--max-age-days", "90")


def test_clean_hook_sessions_weekly_job_is_anchored_for_drift_free_weekly_scheduling():
    job = _job("clean-hook-sessions-weekly")
    assert job.cadence == "every:7d@from=2026-08-28"


def test_bundled_jobs_contains_the_hourly_pdata_sync_job():
    ids = [job.job_id for job in bundled_jobs.BUNDLED_CCSCHED_JOBS]
    assert "pdata-sync-hourly" in ids


def test_pdata_sync_hourly_job_command_and_cadence():
    job = _job("pdata-sync-hourly")
    assert job.command == ("ccst", "pdata", "sync-check", "--all-projects")
    assert job.cadence == "every:1h"
    assert job.coalesce == "one"


def test_pdata_sync_hourly_job_does_not_add_a_launch_notice():
    """A completed run always surfaces regardless of this flag; surface=True only adds the
    "started, running in background" notice. This is the most frequent bundled job (a sweep
    happens at every session start), so that extra notice would be pure duplication."""
    job = _job("pdata-sync-hourly")
    assert job.surface is False


def test_pdata_sync_hourly_job_treats_conflicts_and_no_projects_as_success():
    """Exit 1 = at least one unresolved conflict (the job reporting a real finding, the
    ccst-doctor-drift-weekly precedent); exit 2 = a hard error, which includes the sibling
    "no project databases found" that is the expected state on every run on a machine that
    hasn't adopted pdata (pdata-verify-all's own (0, 2) precedent, 24x more often). Neither must
    count toward auto-suspend; a timeout still does, since worker.py treats timed_out as a crash
    regardless of success_exit_codes."""
    job = _job("pdata-sync-hourly")
    assert job.success_exit_codes == (0, 1, 2)


def test_pdata_sync_hourly_job_does_not_hoard_a_long_catchup_backlog():
    """coalesce="one" pins the run count to 1 however many instants are owed, so catchup_window
    cannot change what this job does - only the "(N overdue)" annotation digest.py prints. A
    day-or-longer window on an hourly cadence turns every post-weekend digest line into
    "(52 overdue)" for a job that reconciled everything in one pass."""
    job = _job("pdata-sync-hourly")
    assert job.catchup_window == "2h"


def test_pdata_sync_hourly_job_allows_time_for_a_full_db_serialize():
    job = _job("pdata-sync-hourly")
    assert job.timeout == "300s"


def _spec_for(job: bundled_jobs.BundledJob, **overrides):
    fields = dict(
        job_id=job.job_id, cadence=job.cadence, coalesce=job.coalesce,
        command=list(job.command), surface=job.surface, enabled=True,
        catchup_window=job.catchup_window, timeout=job.timeout,
        success_exit_codes=job.success_exit_codes,
    )
    fields.update(overrides)
    return validate_job_fields(**fields)


def test_every_bundled_job_is_a_valid_jobspec():
    """`_cmd_ccsched_jobs_install --apply` is the only place a bundled definition meets
    validate_job_fields (its dry run never calls it), so a malformed cadence/duration in this
    file would otherwise only surface when someone provisions it for real on a live machine."""
    for job in bundled_jobs.BUNDLED_CCSCHED_JOBS:
        spec = _spec_for(job)
        assert spec.job_id == job.job_id
        assert diff_from_bundled(spec, job) == ()


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


def test_diff_from_bundled_detail_is_empty_when_nothing_differs():
    job = _job("pdata-verify-all")
    assert diff_from_bundled_detail(_spec_for(job), job) == ()


def test_diff_from_bundled_detail_carries_before_and_after_for_one_field():
    job = _job("pdata-verify-all")
    spec = _spec_for(job, timeout="30s")
    diffs = diff_from_bundled_detail(spec, job)
    assert len(diffs) == 1
    assert diffs[0].field == "timeout"
    assert diffs[0].bundled == job.timeout
    assert diffs[0].current == "30s"


def test_diff_from_bundled_detail_carries_every_differing_field_in_order():
    job = _job("pdata-verify-all")
    spec = _spec_for(job, cadence="every:3d", timeout="30s")
    diffs = diff_from_bundled_detail(spec, job)
    assert [d.field for d in diffs] == ["cadence", "timeout"]
    assert diffs[0].bundled == job.cadence
    assert diffs[0].current == "every:3d"


def test_diff_from_bundled_detail_renders_command_as_a_shell_line_not_a_tuple_repr():
    job = _job("pdata-verify-all")
    spec = _spec_for(job, command=["ccst", "pdata", "verify", "--project", "x y"])
    diffs = diff_from_bundled_detail(spec, job)
    assert len(diffs) == 1
    assert diffs[0].field == "command"
    assert diffs[0].bundled == "ccst pdata verify --all-projects"
    # shlex.join quotes the argument containing a space, unlike a bare join(" ")
    assert diffs[0].current == "ccst pdata verify --project 'x y'"


def test_diff_from_bundled_detail_is_the_single_source_diff_from_bundled_wraps():
    """diff_from_bundled must return exactly the field names diff_from_bundled_detail
    reports, in the same order — the two can never disagree since one wraps the other."""
    job = _job("pdata-verify-all")
    spec = _spec_for(job, cadence="every:3d", timeout="30s")
    assert diff_from_bundled(spec, job) == tuple(
        d.field for d in diff_from_bundled_detail(spec, job)
    )


def test_render_field_diffs_shows_dash_bundled_plus_current_per_field():
    job = _job("pdata-verify-all")
    spec = _spec_for(job, cadence="every:3d", timeout="30s")
    out = render_field_diffs(diff_from_bundled_detail(spec, job))
    assert "cadence:" in out
    assert "- bundled: daily@03:00" in out
    assert "+ current: every:3d" in out
    assert "timeout:" in out
    assert "- bundled: 300s" in out
    assert "+ current: 30s" in out
