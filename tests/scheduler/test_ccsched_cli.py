from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.cli import ccsched
from cc_session_tools.lib.scheduler import registry as reg
from cc_session_tools.lib.scheduler import state as st
from cc_session_tools.lib.scheduler.digest import Outcome
from cc_session_tools.lib.scheduler.jobspec import validate_job_fields


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


def test_show_prints_full_spec(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    res = _run(["show", "tesco"], sched, hooks)
    assert res.returncode == 0
    assert "cadence:" in res.stdout and "daily@09:00" in res.stdout
    assert "timeout:" in res.stdout and "5s" in res.stdout
    assert "catchup_window:" in res.stdout and "7d" in res.stdout
    assert "command:" in res.stdout and "true" in res.stdout


def test_show_unknown_id_errors(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["show", "nope"], sched, hooks)
    assert res.returncode == 2
    assert "unknown job id" in (res.stderr + res.stdout).lower()


def test_disable_then_enable(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    assert _run(["disable", "tesco"], sched, hooks).returncode == 0
    assert _run(["enable", "tesco"], sched, hooks).returncode == 0


def test_enable_unknown_id_errors(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["enable", "ghost"], sched, hooks)
    assert res.returncode == 2


def test_add_with_success_exit_codes(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(
        ["add", "--id", "drift", "--cadence", "daily@09:00",
         "--success-exit-codes", "0,1", "--command", "true"],
        sched, hooks,
    )
    assert res.returncode == 0, res.stderr
    show = _run(["show", "drift"], sched, hooks)
    assert "success_exit_codes:" in show.stdout and "0,1" in show.stdout


def test_add_rejects_bad_success_exit_codes(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    # --success-exit-codes must precede --command: --command uses REMAINDER
    # and swallows every arg after it, same as --coalesce/--cadence/etc above.
    res = _run(
        ["add", "--id", "j", "--cadence", "daily@09:00",
         "--success-exit-codes", "0,nope", "--command", "true"],
        sched, hooks,
    )
    assert res.returncode == 2
    assert "success_exit_codes" in (res.stderr + res.stdout).lower()


def test_edit_updates_success_exit_codes(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    res = _run(["edit", "tesco", "--success-exit-codes", "0,1"], sched, hooks)
    assert res.returncode == 0, res.stderr
    show = _run(["show", "tesco"], sched, hooks)
    assert "0,1" in show.stdout


def test_edit_without_success_exit_codes_preserves_existing(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    _run(["add", "--id", "drift", "--cadence", "daily@09:00",
          "--success-exit-codes", "0,1", "--command", "true"], sched, hooks)
    _run(["edit", "drift", "--timeout", "10s"], sched, hooks)
    show = _run(["show", "drift"], sched, hooks)
    assert "0,1" in show.stdout


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
    assert (hooks / "telemetry.db").is_file()


def _add_direct(
    job_id: str, command: list[str], success_exit_codes: tuple[int, ...] = (0,),
) -> None:
    """Register a job in-process (not via subprocess) so `_cmd_run` can be
    called directly with an injected `notify_push`, matching test_worker.py's
    convention for exercising push behaviour without a real Telegram send."""
    reg.add_job(validate_job_fields(
        job_id=job_id, cadence="daily@09:00", coalesce="one", command=command,
        surface=True, enabled=True, catchup_window="7d", timeout="5s",
        success_exit_codes=success_exit_codes,
    ))


def test_cmd_run_pushes_ran_outcome_with_stdout_captured_unconditionally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§ parity fix: a clean 0-exit `ccsched run` must push RAN with its
    stdout captured unconditionally, same as the worker's `_run_body` -
    previously `_cmd_run` only captured stdout when exit_code != 0 and never
    pushed at all."""
    sched, hooks = _dirs(tmp_path)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(hooks))
    _add_direct("verify", ["sh", "-c", "printf 'all good'"])
    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    rc = ccsched._cmd_run(argparse.Namespace(id="verify"), notify_push=fake_push)
    assert rc == 0
    assert pushed == [("verify", Outcome.RAN, "all good")]
    assert st.load_all_state()["verify"].last_success is not None


def test_cmd_run_pushes_failed_outcome_with_captured_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sched, hooks = _dirs(tmp_path)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(hooks))
    _add_direct("cal", ["sh", "-c", "echo boom 1>&2; exit 1"])
    pushed: list[tuple[str, Outcome, str | None]] = []

    def fake_push(job_id: str, outcome: Outcome, detail: str | None = None) -> bool:
        pushed.append((job_id, outcome, detail))
        return True

    rc = ccsched._cmd_run(argparse.Namespace(id="cal"), notify_push=fake_push)
    assert rc == 1
    assert pushed == [("cal", Outcome.FAILED, "boom")]


def test_cmd_run_manual_failure_never_auto_suspends_despite_now_pushing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual runs stay on `state.record_manual_failure` - no suspend-
    threshold accounting - even though `_cmd_run` now also pushes, matching
    `_run_body`'s push behaviour but not its suspend semantics."""
    sched, hooks = _dirs(tmp_path)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(hooks))
    _add_direct("flaky", ["false"])
    st.save_all_state({"flaky": st.JobState(
        registered_at="2026-01-01T00:00:00Z", last_success=None, last_attempt=None,
        consecutive_failures=9, in_flight=None, suspended=False)})

    rc = ccsched._cmd_run(argparse.Namespace(id="flaky"), notify_push=lambda *a, **k: True)

    assert rc == 1
    after = st.load_all_state()["flaky"]
    assert after.consecutive_failures == 10
    assert after.suspended is False  # manual run never auto-suspends


def test_status_empty_ok(tmp_path: Path) -> None:
    _add_ok(tmp_path)
    sched, hooks = _dirs(tmp_path)
    assert _run(["status"], sched, hooks).returncode == 0


def test_status_shows_error_detail_line_when_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§ 1.5.1 fix: the ledger's `error` column was captured but never
    printed - a failed job's `ccsched status` output gave no clue why it
    failed without going around the CLI to query telemetry.db directly."""
    sched, hooks = _dirs(tmp_path)
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(hooks))
    _add_direct("cal", ["sh", "-c", "echo boom 1>&2; exit 1"])
    ccsched._cmd_run(argparse.Namespace(id="cal"), notify_push=lambda *a, **k: True)

    res = _run(["status", "cal"], sched, hooks)

    assert res.returncode == 0
    assert "boom" in res.stdout


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
    assert (hooks / "telemetry.db").is_file()
    # state advanced (last_success set) and in_flight cleared.
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(sched))
    after = st.load_all_state()["tesco"]
    assert after.last_success is not None
    assert after.in_flight is None


def test_run_job_unknown_id_errors(tmp_path: Path) -> None:
    sched, hooks = _dirs(tmp_path)
    res = _run(["_run-job", "ghost", "--instants", "1"], sched, hooks)
    assert res.returncode == 2
