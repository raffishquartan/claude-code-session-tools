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


def test_ran_completion_ignores_surface_flag() -> None:
    """`surface` no longer gates a completed run's visibility - only
    Outcome.LAUNCHED ("started") still respects it."""
    out = format_digest([_ran("quiet-job", surface=False)])
    assert "ran quiet-job" in out


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


def test_findings_render_as_report_body() -> None:
    r = JobReport(job_id="ccst-doctor-drift-weekly", outcome=Outcome.RAN, surface=True,
                  overdue="", ran=1, deferred=0, expired=0, consecutive_failures=0,
                  findings="WARN data-store:claude-flags\nWARN migration-to-1.0.0:sessions")
    out = format_digest([r])
    assert "ccst-doctor-drift-weekly ran with findings" in out
    assert "WARN data-store:claude-flags" in out
    assert "WARN migration-to-1.0.0:sessions" in out


def test_findings_surface_even_when_job_configured_silent() -> None:
    """Findings are signal, not routine noise - a job with --no-surface must
    still show its findings, the same way FAILED/SUSPENDED always show."""
    r = JobReport(job_id="quiet-drift", outcome=Outcome.RAN, surface=False,
                  overdue="", ran=1, deferred=0, expired=0, consecutive_failures=0,
                  findings="WARN something")
    out = format_digest([r])
    assert "quiet-drift ran with findings" in out
    assert "WARN something" in out


def test_ran_without_findings_unaffected() -> None:
    out = format_digest([_ran("tesco-shop-check")])
    assert "ran with findings" not in out


def test_clean_run_output_renders_neutral_wording_not_findings() -> None:
    r = JobReport(job_id="pdata-verify-all", outcome=Outcome.RAN, surface=True,
                  overdue="", ran=1, deferred=0, expired=0, consecutive_failures=0,
                  output="proj-a: OK (0 issue(s))\nproj-b: OK (0 issue(s))")
    out = format_digest([r])
    assert "✓ ran pdata-verify-all:" in out
    assert "proj-a: OK (0 issue(s))" in out
    assert "ran with findings" not in out


def test_clean_run_output_is_a_single_line_group() -> None:
    """No separate bare '✓ ran X' line followed by a second output block for
    the same run - one run, one line-group."""
    r = JobReport(job_id="pdata-verify-all", outcome=Outcome.RAN, surface=True,
                  overdue="", ran=1, deferred=0, expired=0, consecutive_failures=0,
                  output="proj-a: OK")
    out = format_digest([r])
    assert out.count("ran pdata-verify-all") == 1


def test_clean_run_output_ignores_surface_flag() -> None:
    r = JobReport(job_id="quiet-verify", outcome=Outcome.RAN, surface=False,
                  overdue="", ran=1, deferred=0, expired=0, consecutive_failures=0,
                  output="all good")
    out = format_digest([r])
    assert "quiet-verify" in out
    assert "all good" in out


def test_ran_line_carries_age_suffix_when_set() -> None:
    """A completed run's age must be visible so a cached/stale result (read
    back from an earlier run) is never mistaken for something that just
    happened - e.g. right next to a fresh '▶ launched' line for the same job
    from this same sweep."""
    r = JobReport(job_id="pdata-verify-all", outcome=Outcome.RAN, surface=True,
                  overdue="", ran=1, deferred=0, expired=0, consecutive_failures=0,
                  age="3h ago")
    out = format_digest([r])
    assert "3h ago" in out


def test_ran_line_without_age_is_unaffected() -> None:
    """No age set (the pre-existing default) must render byte-identical to
    before this field existed - no stray suffix text."""
    out = format_digest([_ran("tesco-shop-check")])
    assert "ago" not in out


def test_ran_with_output_line_carries_age_suffix() -> None:
    r = JobReport(job_id="pdata-verify-all", outcome=Outcome.RAN, surface=True,
                  overdue="", ran=1, deferred=0, expired=0, consecutive_failures=0,
                  output="proj-a: OK", age="3h ago")
    out = format_digest([r])
    assert "3h ago" in out
    assert out.count("ran pdata-verify-all") == 1  # still one line-group, not two


def test_findings_line_carries_age_suffix() -> None:
    r = JobReport(job_id="drift", outcome=Outcome.RAN, surface=True, overdue="",
                  ran=1, deferred=0, expired=0, consecutive_failures=0,
                  findings="WARN drifted", age="45m ago")
    out = format_digest([r])
    assert "45m ago" in out


def test_launched_line_carries_age_suffix_when_set() -> None:
    r = JobReport(job_id="calendar-sync", outcome=Outcome.LAUNCHED, surface=True,
                  overdue="", ran=0, deferred=0, expired=0, consecutive_failures=0,
                  age="just now")
    out = format_digest([r])
    assert "just now" in out


def test_launched_line_without_age_is_unaffected() -> None:
    out = format_digest([JobReport(
        job_id="calendar-sync", outcome=Outcome.LAUNCHED, surface=True,
        overdue="", ran=0, deferred=0, expired=0, consecutive_failures=0,
    )])
    assert out == "[cc-scheduler] scheduled-task catch-up:\n▶ launched calendar-sync (running in background)"


def test_coalesced_bare_run_shows_count_and_age_not_n_lines() -> None:
    """Same job running several times unremarkably within one sweep (e.g. an
    hourly no-op job across several short-lived sessions) must render as one
    line with a count, not N near-identical bare ticks."""
    r = JobReport(job_id="ccsched-no-op-demoing-job-visibility", outcome=Outcome.RAN,
                  surface=True, overdue="", ran=1, deferred=0, expired=0,
                  consecutive_failures=0, age="10m ago", count=3)
    out = format_digest([r])
    assert out.count("ccsched-no-op-demoing-job-visibility") == 1
    assert "×3" in out
    assert "10m ago" in out


def test_coalesced_launch_shows_count_and_age_not_n_lines() -> None:
    r = JobReport(job_id="ccsched-no-op-demoing-job-visibility", outcome=Outcome.LAUNCHED,
                  surface=True, overdue="", ran=0, deferred=0, expired=0,
                  consecutive_failures=0, age="10m ago", count=3)
    out = format_digest([r])
    assert out.count("ccsched-no-op-demoing-job-visibility") == 1
    assert "×3" in out
    assert "10m ago" in out


def test_uncoalesced_run_has_no_count_suffix() -> None:
    """count defaults to 0 (the pre-existing shape) - a single ordinary run
    must not grow a '×1' suffix."""
    out = format_digest([_ran("tesco-shop-check")])
    assert "×" not in out


def test_coalesced_clean_output_shows_count_window_and_latest_output_only() -> None:
    """Same job producing captured stdout more than once within one digest
    must render as one line-group carrying a count and the span it covers,
    with only the most recent run's output shown - not N separate blocks."""
    r = JobReport(job_id="pdata-sync-hourly", outcome=Outcome.RAN, surface=True,
                  overdue="", ran=1, deferred=0, expired=0, consecutive_failures=0,
                  output="fast-forwarded 0, published 0, unchanged 10, ... errors 0 of 10 project(s)",
                  age="15m ago", count=2, window="11h")
    out = format_digest([r])
    assert out.count("ran pdata-sync-hourly") == 1
    assert "15m ago" in out
    assert "(2 times in last 11h)" in out
    assert "unchanged 10" in out


def test_uncoalesced_clean_output_has_no_count_or_window_suffix() -> None:
    """A single clean-output run (the pre-existing shape, count defaults to 0)
    must render byte-identical to before this field existed."""
    r = JobReport(job_id="pdata-verify-all", outcome=Outcome.RAN, surface=True,
                  overdue="", ran=1, deferred=0, expired=0, consecutive_failures=0,
                  output="proj-a: OK", age="3h ago")
    out = format_digest([r])
    assert "times in last" not in out
    assert out.count("ran pdata-verify-all") == 1


def test_suspended_job_always_surfaces_even_when_silent() -> None:
    r = JobReport(job_id="broken-job", outcome=Outcome.SUSPENDED, surface=False,
                  overdue="", ran=0, deferred=0, expired=0, consecutive_failures=10)
    out = format_digest([r])
    assert "broken-job auto-suspended after 10 consecutive failures" in out
    assert "ccsched enable broken-job" in out
    assert "ccsched status" in out
