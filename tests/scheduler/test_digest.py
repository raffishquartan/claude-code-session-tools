from __future__ import annotations

from cc_session_tools.lib.scheduler.digest import JobReport, Outcome, format_digest


def _ran(job_id: str, surface: bool = True, overdue: str = "1d") -> JobReport:
    return JobReport(job_id=job_id, outcome=Outcome.RAN, surface=surface,
                     overdue=overdue, ran=1, deferred=0, expired=0,
                     consecutive_failures=0)


def test_empty_reports_is_empty_string() -> None:
    assert format_digest([]) == ""


def test_ran_surfaced_job_appears() -> None:
    out = format_digest([_ran("tesco-shop-check")])
    assert "ran tesco-shop-check" in out
    assert "1d overdue" in out


def test_silent_success_is_omitted() -> None:
    out = format_digest([_ran("quiet-job", surface=False)])
    assert "quiet-job" not in out


def test_failure_always_surfaces_even_when_silent() -> None:
    r = JobReport(job_id="calendar-sync", outcome=Outcome.FAILED, surface=False,
                  overdue="2d", ran=0, deferred=0, expired=0, consecutive_failures=2)
    out = format_digest([r])
    assert "calendar-sync failed" in out
    assert "2nd consecutive" in out
    assert "ccsched status" in out


def test_deferred_backfills_reported() -> None:
    r = JobReport(job_id="foo", outcome=Outcome.RAN, surface=True, overdue="",
                  ran=5, deferred=7, expired=0, consecutive_failures=0)
    out = format_digest([r])
    assert "7 backfills deferred" in out


def test_launched_job_reported() -> None:
    r = JobReport(job_id="calendar-sync", outcome=Outcome.LAUNCHED, surface=True,
                  overdue="", ran=0, deferred=0, expired=0, consecutive_failures=0)
    out = format_digest([r])
    assert "launched calendar-sync" in out
    assert "running in background" in out


def test_silent_launch_is_omitted() -> None:
    r = JobReport(job_id="quiet", outcome=Outcome.LAUNCHED, surface=False,
                  overdue="", ran=0, deferred=0, expired=0, consecutive_failures=0)
    assert "quiet" not in format_digest([r])


def test_unparseable_registry_warning_runs_nothing() -> None:
    out = format_digest([], parse_error="ccsched.db is unreadable: file is not a database")
    assert "job registry failed to load" in out
    assert "no jobs ran" in out


def test_suspended_job_always_surfaces_even_when_silent() -> None:
    r = JobReport(job_id="broken-job", outcome=Outcome.SUSPENDED, surface=False,
                  overdue="", ran=0, deferred=0, expired=0, consecutive_failures=10)
    out = format_digest([r])
    assert "broken-job auto-suspended after 10 consecutive failures" in out
    assert "ccsched enable broken-job" in out
    assert "ccsched status" in out
