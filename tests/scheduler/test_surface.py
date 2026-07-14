from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store
from cc_session_tools.lib.scheduler import cursor
from cc_session_tools.lib.scheduler import ledger as ld
from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler import surface as sf
from cc_session_tools.lib.scheduler.digest import Outcome
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


def test_silent_success_is_marked_non_surfacing(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("quiet", surface=False)
    _run_event("quiet")
    result = sf.surface(session_uuid="s1", now=_NOW)
    # The report carries surface=False so digest omits it; failures would still show.
    rep = next(r for r in result.reports if r.job_id == "quiet")
    assert rep.surface is False


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
