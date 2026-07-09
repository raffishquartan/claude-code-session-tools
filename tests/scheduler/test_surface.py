from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import cursor
from cc_session_tools.lib.scheduler import ledger as ld
from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler import surface as sf
from cc_session_tools.lib.scheduler.digest import Outcome
from cc_session_tools.lib.scheduler.jobspec import validate_job_fields


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


def test_fresh_session_surfaces_all(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _run_event("tesco")
    result = sf.surface(session_uuid="s1")
    assert any(r.job_id == "tesco" and r.outcome is Outcome.RAN for r in result.reports)


def test_cursor_advances_so_second_surface_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _run_event("tesco")
    sf.surface(session_uuid="s1")
    again = sf.surface(session_uuid="s1")
    assert again.reports == []


def test_two_sessions_each_surface_the_same_run_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _run_event("tesco")
    a = sf.surface(session_uuid="a")
    b = sf.surface(session_uuid="b")
    assert any(r.job_id == "tesco" for r in a.reports)
    assert any(r.job_id == "tesco" for r in b.reports)


def test_silent_success_is_marked_non_surfacing(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("quiet", surface=False)
    _run_event("quiet")
    result = sf.surface(session_uuid="s1")
    # The report carries surface=False so digest omits it; failures would still show.
    rep = next(r for r in result.reports if r.job_id == "quiet")
    assert rep.surface is False


def test_failure_event_maps_to_failed_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("cal", surface=False)
    ld.record(ld.LedgerEntry(job_id="cal", event=ld.LedgerEvent.FAIL, owed=1,
                             ran=0, exit_code=1, duration_ms=1, error="boom",
                             consecutive_failures=1))
    result = sf.surface(session_uuid="s1")
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
    result = sf.surface(session_uuid="s1")
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
    result = sf.surface(session_uuid="brand-new-uuid")
    assert result.reports == []
