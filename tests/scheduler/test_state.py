from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import state as st

UTC = timezone.utc


def test_scheduler_dir_honours_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # scheduler_dir now lives in store but is re-exported for callers that used state.
    from cc_session_tools.lib.scheduler import store
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    assert store.scheduler_dir() == tmp_path / "sched"


def test_load_missing_state_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert st.load_all_state() == {}


def test_round_trip_with_in_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    states = {
        "j": st.JobState(
            registered_at="2026-06-20T00:00:00Z",
            last_success="2026-06-20T09:00:00Z",
            last_attempt="2026-06-20T09:00:00Z",
            consecutive_failures=0,
            in_flight=st.InFlight(pid=4321, started_at="2026-06-20T09:00:00Z", instants=3),
        )
    }
    st.save_all_state(states)
    assert st.load_all_state() == states


def test_round_trip_in_flight_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-06-20T00:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=0, in_flight=None)})
    assert st.load_all_state()["j"].in_flight is None


def test_ensure_registered_db_stamps_new_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    now = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    js = st.ensure_registered_db("new-job", now)
    assert js.registered_at == "2026-06-22T08:00:00Z"
    assert js.in_flight is None
    assert js.suspended is False
    assert st.get_state("new-job") == js


def test_ensure_registered_db_leaves_existing_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    existing = st.JobState(registered_at="2026-01-01T00:00:00Z", last_success="2026-02-02T00:00:00Z",
                           last_attempt=None, consecutive_failures=3, in_flight=None)
    st.save_all_state({"j": existing})
    now = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    assert st.ensure_registered_db("j", now) == existing


def test_get_state_missing_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert st.get_state("ghost") is None


def test_set_and_clear_in_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=0, in_flight=None)})
    st.set_in_flight("j", pid=999, started_at="2026-06-22T08:00:00Z", instants=2)
    assert st.get_state("j").in_flight == st.InFlight(pid=999, started_at="2026-06-22T08:00:00Z", instants=2)
    st.clear_in_flight("j")
    assert st.get_state("j").in_flight is None


def test_round_trip_preserves_suspended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    states = {"j": st.JobState(
        registered_at="2026-06-20T00:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=10, in_flight=None, suspended=True)}
    st.save_all_state(states)
    assert st.load_all_state() == states


def test_next_failure_count_increments_below_threshold() -> None:
    assert st.next_failure_count(3, suspended=False, threshold=10) == (4, False, False)


def test_next_failure_count_suspends_at_threshold() -> None:
    assert st.next_failure_count(9, suspended=False, threshold=10) == (10, True, True)


def test_next_failure_count_past_threshold_does_not_resuspend() -> None:
    assert st.next_failure_count(15, suspended=True, threshold=10) == (16, True, False)


def test_clear_suspended_resets_flag_and_leaves_rest_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=12, in_flight=None, suspended=True)})
    st.clear_suspended("j")
    after = st.get_state("j")
    assert after.suspended is False
    assert after.consecutive_failures == 12


def test_clear_suspended_on_unknown_job_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.clear_suspended("ghost")  # must not raise
    assert st.load_all_state() == {}


def test_record_success_resets_streak_preserves_suspended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=st.InFlight(1, "2026-06-22T08:00:00Z", 1),
        suspended=True)})
    st.record_success("j", new_success="2026-06-22T10:00:00Z", attempt_ts="2026-06-22T10:00:00Z")
    after = st.get_state("j")
    assert after.last_success == "2026-06-22T10:00:00Z"
    assert after.consecutive_failures == 0
    assert after.suspended is True            # success does not clear suspension
    assert after.in_flight == st.InFlight(1, "2026-06-22T08:00:00Z", 1)  # untouched


def test_record_failure_increments_and_reports_new_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success="2026-05-05T00:00:00Z",
        last_attempt=None, consecutive_failures=1, in_flight=None, suspended=False)})
    new_c, new_s, newly = st.record_failure(
        "j", attempt_ts="2026-06-22T10:00:00Z", threshold=10)
    assert (new_c, new_s, newly) == (2, False, False)
    after = st.get_state("j")
    assert after.consecutive_failures == 2
    assert after.last_attempt == "2026-06-22T10:00:00Z"
    assert after.last_success == "2026-05-05T00:00:00Z"   # NOT advanced on failure


def test_record_failure_crossing_threshold_reports_newly_suspended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=9, in_flight=None, suspended=False)})
    new_c, new_s, newly = st.record_failure("j", attempt_ts="2026-06-22T10:00:00Z", threshold=10)
    assert (new_c, new_s, newly) == (10, True, True)
    assert st.get_state("j").suspended is True


def test_record_failure_past_threshold_does_not_renotify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=None, suspended=True)})
    _, new_s, newly = st.record_failure("j", attempt_ts="2026-06-22T10:00:00Z", threshold=10)
    assert (new_s, newly) == (True, False)


def test_concurrent_failure_and_success_on_different_jobs_no_cross_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: concurrent state mutations to DIFFERENT jobs must not clobber each
    other's bookkeeping (the whole-file state.json RMW lost updates here)."""
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    ids = [f"job-{i}" for i in range(16)]
    st.save_all_state({jid: st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=0, in_flight=None) for jid in ids})

    errors: list[Exception] = []

    def fail(jid: str) -> None:
        try:
            st.record_failure(jid, attempt_ts="2026-06-22T10:00:00Z", threshold=10)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=fail, args=(jid,)) for jid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    after = st.load_all_state()
    assert all(after[jid].consecutive_failures == 1 for jid in ids)  # every one landed
