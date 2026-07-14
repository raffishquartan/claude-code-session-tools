from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import state as st


def _run(args: list[str], sched_dir: Path, hooks_dir: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["CC_SCHEDULER_DIR"] = str(sched_dir)
    env["CCCS_HOOKS_DIR"] = str(hooks_dir)
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccsched", *args],
        capture_output=True, text=True, env=env,
    )


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "sched", tmp_path / "hooks"


def _add_ok(tmp_path: Path, job_id: str = "tesco") -> subprocess.CompletedProcess[str]:
    sched, hooks = _dirs(tmp_path)
    return _run(
        ["add", "--id", job_id, "--cadence", "daily@09:00",
         "--catchup-window", "7d", "--timeout", "5s",
         "--command", "true"],
        sched, hooks,
    )


def test_add_happy_path(tmp_path: Path) -> None:
    res = _add_ok(tmp_path)
    assert res.returncode == 0, res.stderr
    assert (tmp_path / "sched" / "ccsched.db").is_file()


def test_add_rejects_bad_cadence(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["add", "--id", "j", "--cadence", "hourly", "--command", "true"], sched, hooks)
    assert res.returncode == 2
    assert "cadence" in (res.stderr + res.stdout).lower()


def test_add_rejects_duplicate_id(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    res = _add_ok(tmp_path)
    assert res.returncode == 2
    assert "already exists" in (res.stderr + res.stdout).lower()


def test_add_rejects_empty_command(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["add", "--id", "j", "--cadence", "daily@09:00", "--command"], sched, hooks)
    assert res.returncode != 0


def test_add_rejects_bad_coalesce(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["add", "--id", "j", "--cadence", "daily@09:00",
                "--coalesce", "sometimes", "--command", "true"], sched, hooks)
    assert res.returncode == 2


def test_list_shows_next_due(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    res = _run(["list"], sched, hooks)
    assert res.returncode == 0
    assert "tesco" in res.stdout
    assert "next_due" in res.stdout.lower() or "next" in res.stdout.lower()


def test_disable_then_enable(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    assert _run(["disable", "tesco"], sched, hooks).returncode == 0
    assert _run(["enable", "tesco"], sched, hooks).returncode == 0


def test_enable_unknown_id_errors(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["enable", "ghost"], sched, hooks)
    assert res.returncode == 2


def test_enable_clears_suspension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    st.save_all_state({"tesco": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=None, suspended=True)})
    assert _run(["enable", "tesco"], sched, hooks).returncode == 0
    assert st.load_all_state()["tesco"].suspended is False


def test_run_does_not_clear_existing_suspension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    st.save_all_state({"tesco": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=10, in_flight=None, suspended=True)})
    assert _run(["run", "tesco"], sched, hooks).returncode == 0  # `true` succeeds
    assert st.load_all_state()["tesco"].suspended is True  # still suspended


def test_remove(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    assert _run(["remove", "tesco"], sched, hooks).returncode == 0
    assert "tesco" not in _run(["list"], sched, hooks).stdout


def test_run_records_ledger(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    res = _run(["run", "tesco"], sched, hooks)
    assert res.returncode == 0
    assert (hooks / "fires.jsonl").is_file()


def test_status_empty_ok(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    assert _run(["status"], sched, hooks).returncode == 0


def test_sweep_runs(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    assert _run(["sweep"], sched, hooks).returncode == 0


def test_run_job_worker_executes_and_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The internal _run-job worker runs the command synchronously in its own
    # process and records to the ledger. `true` exits 0 -> a run/backfill event.
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    res = _run(["_run-job", "tesco", "--instants", "1"], sched, hooks)
    assert res.returncode == 0, res.stderr
    assert (hooks / "fires.jsonl").is_file()
    # state advanced (last_success set) and in_flight cleared.
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    after = st.load_all_state()["tesco"]
    assert after.last_success is not None
    assert after.in_flight is None


def test_run_job_unknown_id_errors(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["_run-job", "ghost", "--instants", "1"], sched, hooks)
    assert res.returncode == 2
