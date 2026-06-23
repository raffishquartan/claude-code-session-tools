# tests/scheduler/test_worker.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import ledger as ld
from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler import state as st
from cc_session_tools.lib.scheduler import worker as wk
from cc_session_tools.lib.scheduler.jobspec import validate_job_fields
from cc_session_tools.lib.scheduler.runner import RunOutcome

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))


def _add(job_id: str, cadence: str = "daily@09:00", coalesce: str = "one") -> None:
    reg.add_job(validate_job_fields(
        job_id=job_id, cadence=cadence, coalesce=coalesce, command=["true"],
        surface=True, enabled=True, catchup_window="30d", timeout="5s",
    ))


def _seed(job_id: str, registered_at: str = "2026-06-17T09:00:00Z") -> None:
    st.save_all_state({**st.load_all_state(), job_id: st.JobState(
        registered_at=registered_at, last_success=None, last_attempt=None,
        consecutive_failures=0, in_flight=None)})


def _ok_runner(argv, timeout) -> RunOutcome:
    return RunOutcome(exit_code=0, stdout="", stderr="", duration_ms=1, timed_out=False)


def _fail_runner(argv, timeout) -> RunOutcome:
    return RunOutcome(exit_code=1, stdout="", stderr="boom", duration_ms=1, timed_out=False)


def _timeout_runner(argv, timeout) -> RunOutcome:
    return RunOutcome(exit_code=None, stdout="", stderr="", duration_ms=1, timed_out=True)


def test_success_advances_state_and_clears_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _seed("tesco")
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("tesco", instants=1, now=now, runner=_ok_runner)
    after = st.load_all_state()["tesco"]
    assert after.last_success is not None
    assert after.consecutive_failures == 0
    assert after.in_flight is None  # always cleared


def test_multi_instant_coalesced_run_records_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    # Several daily instants are owed; a coalesce:one run records BACKFILL, not RUN.
    _add("tesco")
    _seed("tesco")  # registered 3 days before now
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("tesco", instants=1, now=now, runner=_ok_runner)
    rows = ld.read_recent(job_id="tesco")
    assert rows[-1]["event"] == ld.LedgerEvent.BACKFILL.value


def test_failure_does_not_advance_and_increments(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("cal")
    _seed("cal")
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("cal", instants=1, now=now, runner=_fail_runner)
    after = st.load_all_state()["cal"]
    assert after.last_success is None
    assert after.consecutive_failures == 1
    assert after.in_flight is None
    assert ld.read_recent(job_id="cal")[-1]["event"] == ld.LedgerEvent.FAIL.value


def test_timeout_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("slow")
    _seed("slow")
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("slow", instants=1, now=now, runner=_timeout_runner)
    after = st.load_all_state()["slow"]
    assert after.last_success is None
    assert after.consecutive_failures == 1


def test_each_runs_up_to_k_times(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("each-job", cadence="every:1h", coalesce="each")
    st.save_all_state({"each-job": st.JobState(
        registered_at="2026-06-20T00:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=0, in_flight=None)})
    calls = {"n": 0}

    def counting(argv, timeout) -> RunOutcome:
        calls["n"] += 1
        return RunOutcome(exit_code=0, stdout="", stderr="", duration_ms=1, timed_out=False)

    now = datetime(2026, 6, 20, 5, 0, tzinfo=UTC)  # 5 hourly instants owed
    wk.run_job("each-job", instants=5, now=now, runner=counting)
    assert calls["n"] == 5


def test_second_consecutive_failure_writes_correct_count_to_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add("cal2")
    # Pre-seed state with one prior consecutive failure
    st.save_all_state({"cal2": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=1, in_flight=None)})
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("cal2", instants=1, now=now, runner=_fail_runner)
    rows = ld.read_recent(job_id="cal2")
    assert rows[-1]["event"] == ld.LedgerEvent.FAIL.value
    assert rows[-1]["consecutive_failures"] == 2


def test_second_worker_exits_when_lock_held_by_live_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("busy")
    _seed("busy")
    st.scheduler_dir().mkdir(parents=True, exist_ok=True)
    import json
    (st.scheduler_dir() / ".run.busy.lock").write_text(
        json.dumps({"pid": os.getpid(), "started": "x"}))  # held by us (alive)
    ran = {"n": 0}

    def runner(argv, timeout) -> RunOutcome:
        ran["n"] += 1
        return RunOutcome(exit_code=0, stdout="", stderr="", duration_ms=1, timed_out=False)

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("busy", instants=1, now=now, runner=runner)
    assert ran["n"] == 0  # lock held by a live holder → worker exited without running
    assert st.load_all_state()["busy"].last_success is None
