from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store
from cc_session_tools.lib.scheduler import cursor
from cc_session_tools.lib.scheduler import ledger as ld
from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler import surface as sf
from cc_session_tools.lib.scheduler.digest import Outcome, format_digest
from cc_session_tools.lib.scheduler.jobspec import validate_job_fields

_NOW = datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))


def _add(job_id: str, surface: bool = True) -> None:
    reg.add_job(validate_job_fields(
        job_id=job_id, cadence="daily@09:00", coalesce="one", command=["true"],
        surface=surface, enabled=True, catchup_window="30d", timeout="5s",
    ))


def _run_event(job_id: str) -> None:
    ld.record(ld.LedgerEntry(job_id=job_id, event=ld.LedgerEvent.RUN, owed=1,
                             ran=1, exit_code=0, duration_ms=1, error=None))


def _insert_catchup_row(tmp_path: Path, *, ts: str, job_id: str, event: str, **extra: object) -> None:
    """Insert one catchup_events row with a caller-chosen ts, bypassing
    ledger.record()'s now()-stamping so staleness/backlog-age tests can pin
    exact ages without depending on the real wall clock."""
    conn = telemetry_store.connect(tmp_path / "hooks")
    conn.execute(
        "INSERT INTO catchup_events "
        "(ts, job_id, event, owed, ran, exit_code, duration_ms, error, consecutive_failures) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ts, job_id, event,
            extra.get("owed", 1), extra.get("ran", 0), extra.get("exit_code"),
            extra.get("duration_ms", 1), extra.get("error"),
            extra.get("consecutive_failures", 0),
        ),
    )
    conn.commit()
    conn.close()


def test_fresh_session_surfaces_all(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _run_event("tesco")
    result = sf.surface(session_uuid="s1", now=_NOW)
    assert any(r.job_id == "tesco" and r.outcome is Outcome.RAN for r in result.reports)


def test_cursor_advances_so_second_surface_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _run_event("tesco")
    sf.surface(session_uuid="s1", now=_NOW)
    again = sf.surface(session_uuid="s1", now=_NOW)
    assert again.reports == []


def test_two_sessions_each_surface_the_same_run_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _run_event("tesco")
    a = sf.surface(session_uuid="a", now=_NOW)
    b = sf.surface(session_uuid="b", now=_NOW)
    assert any(r.job_id == "tesco" for r in a.reports)
    assert any(r.job_id == "tesco" for r in b.reports)


def test_run_completion_ignores_surface_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """§ correction: a bare successful RUN is always visible now, regardless
    of the job's configured `surface` flag - only LAUNCHED remains gated."""
    _add("quiet", surface=False)
    _run_event("quiet")
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "quiet")
    assert rep.outcome is Outcome.RAN
    assert "quiet" in format_digest(result.reports)


def test_backfill_completion_ignores_surface_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("quiet", surface=False)
    ld.record(ld.LedgerEntry(job_id="quiet", event=ld.LedgerEvent.BACKFILL, owed=3,
                             ran=3, exit_code=0, duration_ms=1, error=None))
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "quiet")
    assert rep.outcome is Outcome.RAN
    assert "quiet" in format_digest(result.reports)


def test_backfill_with_captured_output_surfaces_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """§ backfill-output fix: a BACKFILL run (>1 missed interval caught up in
    one attempt - the exact 'machine was off' scenario ccsched exists for)
    must surface its captured stdout the same way a plain RUN does, not drop
    it - see worker.py's _run_body, which always captures stdout regardless
    of RUN vs BACKFILL classification."""
    _add("verify", surface=True)
    ld.record(ld.LedgerEntry(job_id="verify", event=ld.LedgerEvent.BACKFILL, owed=3,
                             ran=3, exit_code=0, duration_ms=1, error="proj-a: OK"))
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "verify")
    assert rep.outcome is Outcome.RAN
    assert rep.output == "proj-a: OK"
    assert rep.findings is None
    assert "proj-a: OK" in format_digest(result.reports)


def test_backfill_with_findings_surfaces_as_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("drift", surface=True)
    ld.record(ld.LedgerEntry(job_id="drift", event=ld.LedgerEvent.BACKFILL, owed=3,
                             ran=3, exit_code=1, duration_ms=1, error="WARN: drifted"))
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "drift")
    assert rep.outcome is Outcome.RAN
    assert rep.findings == "WARN: drifted"
    assert rep.output is None


def test_launch_still_respects_surface_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """LAUNCHED "started" notices are the one outcome the surface flag still
    gates - unaffected by the RAN/BACKFILL always-visible change above."""
    _add("quiet", surface=False)
    ld.record(ld.LedgerEntry(job_id="quiet", event=ld.LedgerEvent.LAUNCH, owed=1,
                             ran=0, exit_code=None, duration_ms=0, error=None))
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "quiet")
    assert rep.outcome is Outcome.LAUNCHED
    assert "quiet" not in format_digest(result.reports)


def test_clean_run_with_stdout_surfaces_as_output_not_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("verify", surface=True)
    ld.record(ld.LedgerEntry(job_id="verify", event=ld.LedgerEvent.RUN, owed=1,
                             ran=1, exit_code=0, duration_ms=1, error="proj-a: OK"))
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "verify")
    assert rep.outcome is Outcome.RAN
    assert rep.output == "proj-a: OK"
    assert rep.findings is None


def test_clean_run_with_stdout_ignores_surface_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("verify", surface=False)
    ld.record(ld.LedgerEntry(job_id="verify", event=ld.LedgerEvent.RUN, owed=1,
                             ran=1, exit_code=0, duration_ms=1, error="proj-a: OK"))
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "verify")
    assert rep.output == "proj-a: OK"
    assert "verify" in format_digest(result.reports)


def test_failure_event_maps_to_failed_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("cal", surface=False)
    ld.record(ld.LedgerEntry(job_id="cal", event=ld.LedgerEvent.FAIL, owed=1,
                             ran=0, exit_code=1, duration_ms=1, error="boom",
                             consecutive_failures=1))
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "cal")
    assert rep.outcome is Outcome.FAILED


def test_second_consecutive_failure_surfaces_correct_ordinal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add("cal", surface=True)
    # First failure
    ld.record(ld.LedgerEntry(job_id="cal", event=ld.LedgerEvent.FAIL, owed=1,
                             ran=0, exit_code=1, duration_ms=1, error="boom",
                             consecutive_failures=1))
    # Second consecutive failure — consecutive_failures=2 on the ledger entry
    ld.record(ld.LedgerEntry(job_id="cal", event=ld.LedgerEvent.FAIL, owed=1,
                             ran=0, exit_code=1, duration_ms=1, error="boom",
                             consecutive_failures=2))
    result = sf.surface(session_uuid="s1", now=_NOW)
    fail_reports = [r for r in result.reports if r.job_id == "cal" and r.outcome is Outcome.FAILED]
    # The second report should carry consecutive_failures=2
    assert any(r.consecutive_failures == 2 for r in fail_reports)


def test_new_session_seed_skips_pre_existing_backlog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for a real incident: ccmsg-dead-letter-sweep had a misconfigured
    command and failed 150+ times over weeks before the config was fixed. Any
    brand-new session_id must not replay that pre-existing backlog as if it just
    happened - only a session that already had a cursor before the backlog was
    written should see it."""
    _add("flaky", surface=True)
    for n in range(1, 10):
        ld.record(ld.LedgerEntry(job_id="flaky", event=ld.LedgerEvent.FAIL, owed=1,
                                 ran=0, exit_code=2, duration_ms=1, error="boom",
                                 consecutive_failures=n))
    cursor.seed_new_session("brand-new-uuid")
    result = sf.surface(session_uuid="brand-new-uuid", now=_NOW)
    assert result.reports == []


def test_run_event_with_error_and_nonzero_exit_surfaces_as_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add("drift", surface=True)
    ld.record(ld.LedgerEntry(job_id="drift", event=ld.LedgerEvent.RUN, owed=1,
                             ran=1, exit_code=1, duration_ms=1, error="WARN: drifted"))
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "drift")
    assert rep.outcome is Outcome.RAN
    assert rep.findings == "WARN: drifted"
    assert rep.surface is True


def test_findings_surface_even_for_a_silent_job(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("drift", surface=False)
    ld.record(ld.LedgerEntry(job_id="drift", event=ld.LedgerEvent.RUN, owed=1,
                             ran=1, exit_code=1, duration_ms=1, error="WARN: drifted"))
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "drift")
    assert rep.findings == "WARN: drifted"
    assert rep.surface is True


def test_findings_never_folded_into_routine_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """A findings-bearing RUN mixed into a large routine backlog must still
    replay individually, exactly like FAILED/SUSPENDED - never swallowed by
    the routine-backlog summary fold."""
    _add("tesco", surface=True)
    _add("drift", surface=True)
    for _ in range(200):
        _run_event("tesco")
    ld.record(ld.LedgerEntry(job_id="drift", event=ld.LedgerEvent.RUN, owed=1,
                             ran=1, exit_code=1, duration_ms=1, error="WARN: drifted"))
    result = sf.surface(session_uuid="s1", now=_NOW)
    findings_reports = [r for r in result.reports if r.findings]
    assert len(findings_reports) == 1
    assert findings_reports[0].job_id == "drift"
    summary_reports = [r for r in result.reports if r.outcome is Outcome.SUMMARY]
    assert len(summary_reports) == 1
    assert summary_reports[0].count == 200  # drift's RUN excluded from the fold


def test_run_event_with_zero_exit_has_no_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco", surface=True)
    _run_event("tesco")
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "tesco")
    assert rep.findings is None


def test_suspend_event_surfaces_as_suspended_report(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("broken-job", surface=False)
    ld.record(ld.LedgerEntry(job_id="broken-job", event=ld.LedgerEvent.SUSPEND, owed=0,
                             ran=0, exit_code=None, duration_ms=0, error=None,
                             consecutive_failures=10))
    result = sf.surface(session_uuid="s1", now=_NOW)
    rep = next(r for r in result.reports if r.job_id == "broken-job")
    assert rep.outcome is Outcome.SUSPENDED
    assert rep.consecutive_failures == 10


# --- Dormant-session staleness/replay fix -----------------------------------


def test_large_routine_backlog_collapses_to_one_summary_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dormant session catching up on 500 routine RUN events must see one
    summary report, not 500 individual ones — and the cursor must still
    advance past all 500 so nothing replays twice."""
    _add("tesco", surface=True)
    for _ in range(500):
        _run_event("tesco")
    result = sf.surface(session_uuid="s1", now=_NOW)
    summary_reports = [r for r in result.reports if r.outcome is Outcome.SUMMARY]
    assert len(summary_reports) == 1
    assert summary_reports[0].count == 500
    assert not any(r.outcome is Outcome.RAN for r in result.reports)
    # Cursor fully advanced: a second surface for the same session sees nothing new.
    again = sf.surface(session_uuid="s1", now=_NOW)
    assert again.reports == []


def test_stale_failed_entry_replays_in_full_with_age_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single FAILED entry from ~20 days ago, mixed in among otherwise-routine
    entries, must be shown in full with an age suffix — never folded into a
    summary, regardless of how old it is."""
    old_ts = (_NOW - timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _insert_catchup_row(
        tmp_path, ts=old_ts, job_id="cal", event="fail", ran=0, exit_code=1,
        error="boom", consecutive_failures=1,
    )
    result = sf.surface(session_uuid="s1", now=_NOW)
    fail_reports = [r for r in result.reports if r.outcome is Outcome.FAILED]
    assert len(fail_reports) == 1
    assert fail_reports[0].job_id == "cal"
    assert fail_reports[0].age == "20d ago"
    assert not any(r.outcome is Outcome.SUMMARY for r in result.reports)


def test_many_stale_routine_entries_and_one_recent_failure_coexist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both paths must coexist in one digest: a large stale routine backlog
    collapses to a summary while a recent FAILED entry (mixed into the same
    backlog) still replays individually with its own age suffix."""
    old_ts = (_NOW - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_ts = (_NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for _ in range(150):
        _insert_catchup_row(tmp_path, ts=old_ts, job_id="tesco", event="run", ran=1)
    _insert_catchup_row(
        tmp_path, ts=recent_ts, job_id="cal", event="fail", ran=0, exit_code=1,
        error="boom", consecutive_failures=1,
    )
    result = sf.surface(session_uuid="s1", now=_NOW)
    summary_reports = [r for r in result.reports if r.outcome is Outcome.SUMMARY]
    fail_reports = [r for r in result.reports if r.outcome is Outcome.FAILED]
    assert len(summary_reports) == 1
    assert summary_reports[0].count == 150
    assert len(fail_reports) == 1
    assert fail_reports[0].job_id == "cal"
    assert fail_reports[0].age == "1h ago"


# --- Same-job coalescing + chronological order fix ---------------------------


def test_repeated_launch_and_run_for_same_job_coalesce_to_one_line_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the reported symptom: a fast-cadence job that gets
    reconciled by several short-lived sessions within one hour must not
    replay as N near-identical '✓ ran'/'▶ launched' lines - each event type
    coalesces to one line per job, carrying a count."""
    _add("no-op", surface=True)
    base = _NOW - timedelta(hours=1)
    for n in range(3):
        _insert_catchup_row(
            tmp_path, ts=(base + timedelta(minutes=20 * n)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            job_id="no-op", event="launch",
        )
        _insert_catchup_row(
            tmp_path, ts=(base + timedelta(minutes=20 * n + 1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            job_id="no-op", event="run", ran=1, exit_code=0,
        )
    result = sf.surface(session_uuid="s1", now=_NOW)
    launched = [r for r in result.reports if r.outcome is Outcome.LAUNCHED]
    ran = [r for r in result.reports if r.outcome is Outcome.RAN]
    assert len(launched) == 1
    assert launched[0].count == 3
    assert len(ran) == 1
    assert ran[0].count == 3
    assert len(result.reports) == 2
    # Chronological: the last RUN (base+41m) happened after the last LAUNCH
    # (base+40m), so the coalesced RAN line sorts after the coalesced
    # LAUNCHED line, matching real chronological order rather than the
    # (bare-vs-output-bearing) branch each event happened to take.
    assert result.reports.index(launched[0]) < result.reports.index(ran[0])


def test_events_from_different_branches_sort_chronologically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FAIL (always individual) and a bare LAUNCH (routine bucket) mixed in
    one sweep must render in the order they actually happened, not in the
    order surface()'s internal branches happen to process them."""
    _add("flaky", surface=True)
    _add("no-op", surface=True)
    t1 = (_NOW - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t2 = (_NOW - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t3 = (_NOW - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _insert_catchup_row(tmp_path, ts=t1, job_id="no-op", event="launch")
    _insert_catchup_row(
        tmp_path, ts=t2, job_id="flaky", event="fail", ran=0, exit_code=1,
        error="boom", consecutive_failures=1,
    )
    _insert_catchup_row(
        tmp_path, ts=t3, job_id="no-op", event="run", ran=1, exit_code=0, error="did a thing",
    )
    result = sf.surface(session_uuid="s1", now=_NOW)
    order = [(r.job_id, r.outcome) for r in result.reports]
    assert order == [
        ("no-op", Outcome.LAUNCHED),
        ("flaky", Outcome.FAILED),
        ("no-op", Outcome.RAN),
    ]


# --- SessionStart 24h rolling lookback ---------------------------------------


def test_lookback_none_keeps_exact_cursor_only_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Baseline: with no lookback (UserPromptSubmit's case), an entry that
    predates the seeded cursor is invisible - exactly as before this change."""
    _add("tesco")
    recent_ts = (_NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _insert_catchup_row(tmp_path, ts=recent_ts, job_id="tesco", event="run", ran=1)
    cursor.seed_new_session("s1")
    result = sf.surface(session_uuid="s1", now=_NOW)
    assert result.reports == []


def test_lookback_surfaces_entry_that_predates_seeded_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§ widen fix: with a 24h lookback (SessionStart's case), the same
    pre-seed entry from test_lookback_none_keeps_exact_cursor_only_behaviour
    above IS surfaced, since it falls within the window."""
    _add("tesco")
    recent_ts = (_NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _insert_catchup_row(tmp_path, ts=recent_ts, job_id="tesco", event="run", ran=1)
    cursor.seed_new_session("s1")
    result = sf.surface(session_uuid="s1", now=_NOW, lookback=timedelta(hours=24))
    assert any(r.job_id == "tesco" for r in result.reports)


def test_lookback_excludes_entries_older_than_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add("tesco")
    old_ts = (_NOW - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _insert_catchup_row(tmp_path, ts=old_ts, job_id="tesco", event="run", ran=1)
    cursor.seed_new_session("s1")
    result = sf.surface(session_uuid="s1", now=_NOW, lookback=timedelta(hours=24))
    assert result.reports == []


def test_lookback_surfaces_a_fail_entry_that_predates_seeded_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§ widen fix, FAIL variant: a job crash recorded before this brand-new
    session's cursor was seeded must still surface via the same 24h lookback
    mechanism that test_lookback_surfaces_entry_that_predates_seeded_cursor
    proves for a RUN entry above - a FAIL must not be lost just because no
    live session was open when the worker crashed."""
    _add("cal")
    recent_ts = (_NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _insert_catchup_row(
        tmp_path, ts=recent_ts, job_id="cal", event="fail", ran=0, exit_code=1,
        error="boom", consecutive_failures=1,
    )
    cursor.seed_new_session("s1")
    result = sf.surface(session_uuid="s1", now=_NOW, lookback=timedelta(hours=24))
    fail_reports = [r for r in result.reports if r.job_id == "cal" and r.outcome is Outcome.FAILED]
    assert len(fail_reports) == 1


def test_lookback_does_not_duplicate_entries_within_one_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry reachable via both the 24h floor and the normal cursor path
    must appear exactly once in a single surface() call."""
    _add("tesco")
    recent_ts = (_NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _insert_catchup_row(tmp_path, ts=recent_ts, job_id="tesco", event="run", ran=1)
    result = sf.surface(session_uuid="s1", now=_NOW, lookback=timedelta(hours=24))
    matches = [r for r in result.reports if r.job_id == "tesco"]
    assert len(matches) == 1


def test_lookback_still_advances_cursor_to_the_true_tip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a SessionStart lookback call, the session's cursor lands on the
    ledger's current tip exactly as it does today - so the next
    (non-lookback) UserPromptSubmit call in this session sees nothing new."""
    _add("tesco")
    recent_ts = (_NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _insert_catchup_row(tmp_path, ts=recent_ts, job_id="tesco", event="run", ran=1)
    cursor.seed_new_session("s1")
    sf.surface(session_uuid="s1", now=_NOW, lookback=timedelta(hours=24))
    again = sf.surface(session_uuid="s1", now=_NOW)
    assert again.reports == []
