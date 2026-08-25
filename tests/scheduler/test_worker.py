# tests/scheduler/test_worker.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import ledger as ld
from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler import state as st
from cc_session_tools.lib.scheduler import store
from cc_session_tools.lib.scheduler import worker as wk
from cc_session_tools.lib.scheduler.digest import Outcome
from cc_session_tools.lib.scheduler.jobspec import validate_job_fields
from cc_session_tools.lib.scheduler.runner import RunOutcome

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))


def _add(
    job_id: str, cadence: str = "daily@09:00", coalesce: str = "one",
    success_exit_codes: tuple[int, ...] = (0,), surface: bool = True,
) -> None:
    reg.add_job(validate_job_fields(
        job_id=job_id, cadence=cadence, coalesce=coalesce, command=["true"],
        surface=surface, enabled=True, catchup_window="30d", timeout="5s",
        success_exit_codes=success_exit_codes,
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


def test_tenth_consecutive_failure_suspends_and_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("broken")
    st.save_all_state({"broken": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=9, in_flight=None, suspended=False)})
    notified: list[tuple[str, int]] = []

    def fake_notify(job_id: str, consecutive_failures: int) -> bool:
        notified.append((job_id, consecutive_failures))
        return True

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("broken", instants=1, now=now, runner=_fail_runner, notify_suspended=fake_notify)

    after = st.load_all_state()["broken"]
    assert after.consecutive_failures == 10
    assert after.suspended is True
    assert notified == [("broken", 10)]
    rows = ld.read_recent(job_id="broken")
    assert rows[-1]["event"] == ld.LedgerEvent.SUSPEND.value


def test_eleventh_consecutive_failure_does_not_renotify(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("broken")
    st.save_all_state({"broken": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=None, suspended=True)})
    notified: list[tuple[str, int]] = []

    def fake_notify(job_id: str, consecutive_failures: int) -> bool:
        notified.append((job_id, consecutive_failures))
        return True

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("broken", instants=1, now=now, runner=_fail_runner, notify_suspended=fake_notify)

    assert notified == []  # already suspended — no repeat push
    rows = ld.read_recent(job_id="broken")
    assert rows[-1]["event"] == ld.LedgerEvent.FAIL.value  # still a FAIL, no new SUSPEND


def test_healthy_job_never_suspends(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _seed("tesco")
    notified: list[tuple[str, int]] = []
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("tesco", instants=1, now=now, runner=_ok_runner,
               notify_suspended=lambda j, n: notified.append((j, n)) or True)
    after = st.load_all_state()["tesco"]
    assert after.suspended is False
    assert notified == []


def test_success_preserves_existing_suspended_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("flaky")
    st.save_all_state({"flaky": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=None, suspended=True)})
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("flaky", instants=1, now=now, runner=_ok_runner)
    after = st.load_all_state()["flaky"]
    assert after.consecutive_failures == 0  # success still resets the streak
    assert after.suspended is True  # but does not clear suspension


def test_expected_nonzero_exit_does_not_count_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A drift-monitor-style job with success_exit_codes=(0, 1) exiting 1 must
    advance last_success and reset the failure streak, not accumulate toward
    auto-suspend - the whole point of success_exit_codes."""
    _add("drift", success_exit_codes=(0, 1))
    _seed("drift")

    def findings_runner(argv, timeout) -> RunOutcome:
        return RunOutcome(exit_code=1, stdout="WARN: something drifted", stderr="",
                          duration_ms=1, timed_out=False)

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("drift", instants=1, now=now, runner=findings_runner)
    after = st.load_all_state()["drift"]
    assert after.last_success is not None
    assert after.consecutive_failures == 0
    assert after.suspended is False
    rows = ld.read_recent(job_id="drift")
    assert rows[-1]["event"] == ld.LedgerEvent.BACKFILL.value or rows[-1]["event"] == ld.LedgerEvent.RUN.value
    assert rows[-1]["error"] == "WARN: something drifted"


def test_exit_code_outside_success_set_still_counts_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """success_exit_codes=(0, 1) does not mean "anything goes" - exit 2 (a real
    crash) must still increment the failure streak exactly as before."""
    _add("drift", success_exit_codes=(0, 1))
    _seed("drift")

    def crash_runner(argv, timeout) -> RunOutcome:
        return RunOutcome(exit_code=2, stdout="", stderr="traceback", duration_ms=1, timed_out=False)

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("drift", instants=1, now=now, runner=crash_runner)
    after = st.load_all_state()["drift"]
    assert after.last_success is None
    assert after.consecutive_failures == 1
    assert ld.read_recent(job_id="drift")[-1]["event"] == ld.LedgerEvent.FAIL.value


def test_plain_zero_exit_records_no_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _seed("tesco")
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("tesco", instants=1, now=now, runner=_ok_runner)
    assert ld.read_recent(job_id="tesco")[-1]["error"] is None


def test_clean_zero_exit_with_stdout_is_now_captured(monkeypatch: pytest.MonkeyPatch) -> None:
    """§ correction: a 0-exit run's stdout used to be discarded entirely
    (only a nonzero exit's stdout was captured as "findings"); now it is
    captured exactly like the nonzero-exit case, so a verify-style job's
    "all OK" confirmation is not silently thrown away."""
    _add("verify")
    _seed("verify")

    def clean_runner(argv, timeout) -> RunOutcome:
        return RunOutcome(exit_code=0, stdout="proj-a: OK (0 issue(s))", stderr="",
                          duration_ms=1, timed_out=False)

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("verify", instants=1, now=now, runner=clean_runner)
    assert ld.read_recent(job_id="verify")[-1]["error"] == "proj-a: OK (0 issue(s))"


def test_second_worker_exits_when_lock_held_by_live_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("busy")
    _seed("busy")
    store.scheduler_dir().mkdir(parents=True, exist_ok=True)
    import json
    (store.scheduler_dir() / ".run.busy.lock").write_text(
        json.dumps({"pid": os.getpid(), "started": "x"}))  # held by us (alive)
    ran = {"n": 0}

    def runner(argv, timeout) -> RunOutcome:
        ran["n"] += 1
        return RunOutcome(exit_code=0, stdout="", stderr="", duration_ms=1, timed_out=False)

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("busy", instants=1, now=now, runner=runner)
    assert ran["n"] == 0  # lock held by a live holder → worker exited without running
    assert st.load_all_state()["busy"].last_success is None


def test_successful_run_pushes_ran_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    _seed("tesco")
    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("tesco", instants=1, now=now, runner=_ok_runner, notify_push=fake_push)
    assert pushed == [("tesco", Outcome.RAN, None)]


def test_successful_run_pushes_regardless_of_surface_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """§ correction: `surface` no longer gates push (or digest) visibility
    for a completed run - a --no-surface job still pushes on every clean
    run."""
    _add("quiet", surface=False)
    _seed("quiet")
    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("quiet", instants=1, now=now, runner=_ok_runner, notify_push=fake_push)
    assert pushed == [("quiet", Outcome.RAN, None)]


def test_run_with_captured_output_pushes_with_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("verify")
    _seed("verify")

    def clean_runner(argv, timeout) -> RunOutcome:
        return RunOutcome(exit_code=0, stdout="all good", stderr="", duration_ms=1,
                          timed_out=False)

    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("verify", instants=1, now=now, runner=clean_runner, notify_push=fake_push)
    assert pushed == [("verify", Outcome.RAN, "all good")]


def test_ordinary_failure_pushes_failed_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("cal")
    _seed("cal")
    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("cal", instants=1, now=now, runner=_fail_runner, notify_push=fake_push)
    assert pushed == [("cal", Outcome.FAILED, "boom")]


def test_newly_suspended_does_not_also_push_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """SUSPENDED already pushes via notify_suspended - a FAILED push for the
    same crash would double-notify."""
    _add("broken")
    st.save_all_state({"broken": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=9, in_flight=None, suspended=False)})
    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("broken", instants=1, now=now, runner=_fail_runner,
               notify_suspended=lambda j, n: True, notify_push=fake_push)
    assert pushed == []


def test_classify_outcome_success_captures_stdout_unconditionally_and_pushes() -> None:
    spec = validate_job_fields(
        job_id="verify", cadence="daily@09:00", coalesce="one", command=["true"],
        surface=True, enabled=True, catchup_window="7d", timeout="5s",
        success_exit_codes=(0,),
    )
    outcome = RunOutcome(exit_code=0, stdout="all good", stderr="", duration_ms=1, timed_out=False)
    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    result = wk.classify_outcome(spec, outcome, notify_push=fake_push)
    assert result.crashed is False
    assert result.event == ld.LedgerEvent.RUN
    assert result.detail == "all good"
    assert pushed == [("verify", Outcome.RAN, "all good")]


def test_classify_outcome_crash_captures_truncated_stderr_and_pushes() -> None:
    spec = validate_job_fields(
        job_id="cal", cadence="daily@09:00", coalesce="one", command=["false"],
        surface=True, enabled=True, catchup_window="7d", timeout="5s",
        success_exit_codes=(0,),
    )
    outcome = RunOutcome(exit_code=1, stdout="", stderr="x" * 300, duration_ms=1, timed_out=False)
    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    result = wk.classify_outcome(spec, outcome, notify_push=fake_push)
    assert result.crashed is True
    assert result.event == ld.LedgerEvent.FAIL
    assert result.detail == "x" * 200
    assert pushed == [("cal", Outcome.FAILED, "x" * 200)]


def test_classify_outcome_timeout_with_no_stderr_falls_back_to_timed_out_text() -> None:
    spec = validate_job_fields(
        job_id="slow", cadence="daily@09:00", coalesce="one", command=["sleep", "10"],
        surface=True, enabled=True, catchup_window="1s", timeout="1s",
        success_exit_codes=(0,),
    )
    outcome = RunOutcome(exit_code=None, stdout="", stderr="", duration_ms=1000, timed_out=True)
    result = wk.classify_outcome(spec, outcome, notify_push=lambda *a, **k: True)
    assert result.crashed is True
    assert result.detail == "timed out"


def test_classify_outcome_push_false_suppresses_the_push() -> None:
    spec = validate_job_fields(
        job_id="broken", cadence="daily@09:00", coalesce="one", command=["false"],
        surface=True, enabled=True, catchup_window="7d", timeout="5s",
        success_exit_codes=(0,),
    )
    outcome = RunOutcome(exit_code=1, stdout="", stderr="boom", duration_ms=1, timed_out=False)
    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    result = wk.classify_outcome(spec, outcome, notify_push=fake_push, push=False)
    assert result.crashed is True
    assert result.detail == "boom"
    assert pushed == []


def test_classify_outcome_zero_attempt_is_a_crash_with_no_detail() -> None:
    """Mirrors `_run_body`'s zero-attempt edge case (a coalesce:each loop that
    ran zero times) - `outcome=None` still classifies as crashed and still
    pushes, with no detail to report."""
    spec = validate_job_fields(
        job_id="zero", cadence="daily@09:00", coalesce="each", command=["true"],
        surface=True, enabled=True, catchup_window="7d", timeout="5s",
        success_exit_codes=(0,),
    )
    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    result = wk.classify_outcome(spec, None, notify_push=fake_push)
    assert result.crashed is True
    assert result.detail is None
    assert pushed == [("zero", Outcome.FAILED, None)]


def test_lock_wraps_sql_state_mutations_r3(monkeypatch: pytest.MonkeyPatch) -> None:
    """R3: the file-based in-flight lock still wraps the (now SQL) state writes.
    A live lock holder means the worker exits without touching state at all."""
    _add("wrapped")
    store.scheduler_dir().mkdir(parents=True, exist_ok=True)
    import json as _json
    (store.scheduler_dir() / ".run.wrapped.lock").write_text(
        _json.dumps({"pid": os.getpid(), "started": "x"}))  # held by us (alive)
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    wk.run_job("wrapped", instants=1, now=now, runner=_ok_runner)
    # No state row was even created — the worker returned at the lock, before
    # ensure_registered_db.
    assert st.get_state("wrapped") is None
