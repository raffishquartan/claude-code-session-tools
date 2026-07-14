# tests/scheduler/test_reconcile.py
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import ledger as ld
from cc_session_tools.lib.scheduler import reconcile as rc
from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler import state as st
from cc_session_tools.lib.scheduler.jobspec import validate_job_fields

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


class _Spawn:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        return 12345


def test_overdue_job_is_launched_with_instants(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("tesco")
    st.save_all_state({"tesco": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=0, in_flight=None)})
    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = rc.reconcile_and_launch(now=now, spawn=spawn)
    assert "tesco" in result.launched
    assert spawn.calls and spawn.calls[0][:3] == ["ccsched", "_run-job", "tesco"]
    assert "--instants" in spawn.calls[0]
    # A LAUNCH event is recorded.
    rows = ld.read_recent(job_id="tesco")
    assert rows[-1]["event"] == ld.LedgerEvent.LAUNCH.value


def test_in_flight_job_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    _add("busy")
    st.save_all_state({"busy": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=0,
        in_flight=st.InFlight(pid=os.getpid(), started_at="2026-06-20T09:00:00Z", instants=1))})
    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = rc.reconcile_and_launch(now=now, spawn=spawn)
    assert "busy" not in result.launched
    assert spawn.calls == []


def test_nothing_owed_is_not_launched(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("fresh")
    st.save_all_state({"fresh": st.JobState(
        registered_at="2026-06-20T09:30:00Z", last_success="2026-06-20T09:30:00Z",
        last_attempt="2026-06-20T09:30:00Z", consecutive_failures=0, in_flight=None)})
    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)  # daily@09:00 not yet due again
    result = rc.reconcile_and_launch(now=now, spawn=spawn)
    assert result.launched == []


def test_disabled_job_not_launched(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("off")
    reg.set_enabled("off", False)
    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    rc.reconcile_and_launch(now=now, spawn=spawn)
    assert spawn.calls == []


def test_suspended_job_not_launched(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("broken")
    st.save_all_state({"broken": st.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=10, in_flight=None, suspended=True)})
    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = rc.reconcile_and_launch(now=now, spawn=spawn)
    assert "broken" not in result.launched
    assert spawn.calls == []


def test_launch_cap_defers_overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(3):
        _add(f"job-{i}")
        st.save_all_state({**st.load_all_state(), f"job-{i}": st.JobState(
            registered_at="2026-06-17T09:00:00Z", last_success=None,
            last_attempt=None, consecutive_failures=0, in_flight=None)})
    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = rc.reconcile_and_launch(now=now, spawn=spawn, per_sweep_cap=2)
    assert len(result.launched) == 2
    assert len(spawn.calls) == 2
    # The third job records a DEFER event rather than launching.
    deferred = [r for r in ld.read_recent() if r["event"] == ld.LedgerEvent.DEFER.value]
    assert len(deferred) == 1


def test_registry_load_failure_surfaces_and_launches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cc_session_tools.lib.scheduler import store
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))
    store.scheduler_dir().mkdir(parents=True, exist_ok=True)
    # A non-SQLite file at the DB path makes registry.load_registry() raise; the
    # reconcile boundary must convert that to parse_error, not crash.
    store.db_path().write_bytes(b"this is not a sqlite database file")
    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    result = rc.reconcile_and_launch(now=now, spawn=spawn)
    assert result.parse_error is not None
    assert result.launched == []
    assert spawn.calls == []


def test_reconcile_concurrent_with_worker_setinflight_no_loss_r4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: a reconcile sweep (ensure_registered for several jobs) running
    concurrently with a worker stamping in_flight on a DIFFERENT job must not
    drop the worker's update. With per-row writes this is automatic."""
    import threading
    from cc_session_tools.lib.scheduler import state as st
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))
    # Several never-seen jobs for reconcile to register, plus one job the
    # "worker" stamps in_flight on.
    for i in range(8):
        _add(f"job-{i}")
    _add("worker-job")
    st.save_all_state({"worker-job": st.JobState(
        registered_at="2026-06-20T09:00:00Z", last_success="2026-06-20T09:00:00Z",
        last_attempt=None, consecutive_failures=0, in_flight=None)})

    spawn = _Spawn()
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    barrier = threading.Barrier(2)

    def do_reconcile() -> None:
        barrier.wait()
        rc.reconcile_and_launch(now=now, spawn=spawn)

    def do_worker_stamp() -> None:
        barrier.wait()
        st.set_in_flight("worker-job", pid=4242, started_at="2026-06-20T10:00:00Z", instants=1)

    t1 = threading.Thread(target=do_reconcile)
    t2 = threading.Thread(target=do_worker_stamp)
    t1.start(); t2.start(); t1.join(); t2.join()

    # The worker's in_flight stamp survived the concurrent reconcile.
    assert st.get_state("worker-job").in_flight == st.InFlight(
        pid=4242, started_at="2026-06-20T10:00:00Z", instants=1)
    # And reconcile registered the never-seen jobs.
    after = st.load_all_state()
    assert all(f"job-{i}" in after for i in range(8))
