from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import state as st

UTC = timezone.utc


def test_scheduler_dir_honours_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    assert st.scheduler_dir() == tmp_path / "sched"


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
    assert not (tmp_path / "state.json.tmp").exists()
    assert st.load_all_state() == states


def test_round_trip_in_flight_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    states = {
        "j": st.JobState(
            registered_at="2026-06-20T00:00:00Z", last_success=None,
            last_attempt=None, consecutive_failures=0, in_flight=None,
        )
    }
    st.save_all_state(states)
    assert st.load_all_state()["j"].in_flight is None


def test_ensure_registered_stamps_new_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    now = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    states: dict[str, st.JobState] = {}
    js = st.ensure_registered(states, "new-job", now)
    assert js.registered_at == "2026-06-22T08:00:00Z"
    assert js.in_flight is None
    assert states["new-job"].registered_at == "2026-06-22T08:00:00Z"


def test_ensure_registered_leaves_existing_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    now = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    existing = st.JobState(registered_at="2026-01-01T00:00:00Z", last_success=None,
                           last_attempt=None, consecutive_failures=0, in_flight=None)
    states = {"j": existing}
    assert st.ensure_registered(states, "j", now) == existing


def test_set_and_clear_in_flight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=0, in_flight=None)})
    st.set_in_flight("j", pid=999, started_at="2026-06-22T08:00:00Z", instants=2)
    loaded = st.load_all_state()["j"]
    assert loaded.in_flight == st.InFlight(pid=999, started_at="2026-06-22T08:00:00Z", instants=2)
    st.clear_in_flight("j")
    assert st.load_all_state()["j"].in_flight is None


def test_round_trip_preserves_suspended(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    states = {
        "j": st.JobState(
            registered_at="2026-06-20T00:00:00Z", last_success=None,
            last_attempt=None, consecutive_failures=10, in_flight=None,
            suspended=True,
        )
    }
    st.save_all_state(states)
    assert st.load_all_state() == states


def test_default_suspended_is_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    now = datetime(2026, 6, 22, 8, 0, tzinfo=UTC)
    js = st.ensure_registered({}, "new-job", now)
    assert js.suspended is False


def test_next_failure_count_increments_below_threshold() -> None:
    new_count, suspended, newly = st.next_failure_count(3, suspended=False, threshold=10)
    assert (new_count, suspended, newly) == (4, False, False)


def test_next_failure_count_suspends_at_threshold() -> None:
    new_count, suspended, newly = st.next_failure_count(9, suspended=False, threshold=10)
    assert (new_count, suspended, newly) == (10, True, True)


def test_next_failure_count_past_threshold_does_not_resuspend() -> None:
    new_count, suspended, newly = st.next_failure_count(15, suspended=True, threshold=10)
    assert (new_count, suspended, newly) == (16, True, False)


def test_clear_suspended_resets_flag_and_leaves_rest_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.save_all_state({"j": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=12, in_flight=None, suspended=True)})
    st.clear_suspended("j")
    after = st.load_all_state()["j"]
    assert after.suspended is False
    assert after.consecutive_failures == 12


def test_clear_suspended_on_unknown_job_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    st.clear_suspended("ghost")
    assert st.load_all_state() == {}
