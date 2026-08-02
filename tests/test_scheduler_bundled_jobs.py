from __future__ import annotations

from cc_session_tools.lib.scheduler import bundled_jobs


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
