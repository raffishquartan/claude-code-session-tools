# tests/scheduler/test_catchup_hook.py
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cccs_hooks import catchup
from cc_session_tools.lib.scheduler import ledger, reconcile, registry, state
from cc_session_tools.lib.scheduler.jobspec import validate_job_fields


@pytest.fixture(autouse=True)
def _dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))


def _stdin(monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    out: list[str] = []
    monkeypatch.setattr(catchup, "_emit", lambda ctx, event: out.append(ctx))
    return out


class _Spawn:
    """A mocked detached-spawn: records launches, returns instantly, never runs."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        return 4242


def test_session_start_launches_detached_and_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.add_job(validate_job_fields(
        job_id="tesco", cadence="daily@09:00", coalesce="one", command=["true"],
        surface=True, enabled=True, catchup_window="30d", timeout="5s",
    ))
    state.save_all_state({"tesco": state.JobState(
        registered_at="2026-06-17T09:00:00Z", last_success=None,
        last_attempt=None, consecutive_failures=0, in_flight=None)})
    monkeypatch.setattr(catchup, "_now", lambda: datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc))
    # The hook must launch via a detached spawn, never run the command itself.
    spawn = _Spawn()
    monkeypatch.setattr(reconcile, "spawn_detached", spawn)
    _stdin(monkeypatch, {"hookEventName": "SessionStart", "session_id": "u", "cwd": "/tmp"})
    _capture(monkeypatch)
    assert catchup.main() == 0
    assert spawn.calls and spawn.calls[0][:3] == ["ccsched", "_run-job", "tesco"]
    # No worker ran in-process, so last_success is NOT advanced by the hook itself.
    assert state.load_all_state()["tesco"].last_success is None


def test_surface_emits_digest_from_ledger_since_cursor_and_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.add_job(validate_job_fields(
        job_id="tesco", cadence="daily@09:00", coalesce="one", command=["true"],
        surface=True, enabled=True, catchup_window="30d", timeout="5s",
    ))
    # Seed the ledger as if a worker had already completed a run.
    ledger.record(ledger.LedgerEntry(
        job_id="tesco", event=ledger.LedgerEvent.RUN, owed=1, ran=1,
        exit_code=0, duration_ms=1, error=None))
    monkeypatch.setattr(catchup, "_now", lambda: datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc))
    # Avoid launching anything real on this UserPromptSubmit reap.
    monkeypatch.setattr(reconcile, "spawn_detached", _Spawn())
    _stdin(monkeypatch, {"hookEventName": "UserPromptSubmit", "session_id": "u", "cwd": "/tmp"})
    out = _capture(monkeypatch)
    assert catchup.main() == 0
    assert any("tesco" in e for e in out)
    # Surfaced once: a second reap for the same session sees nothing new.
    out2 = _capture(monkeypatch)
    _stdin(monkeypatch, {"hookEventName": "UserPromptSubmit", "session_id": "u", "cwd": "/tmp"})
    assert catchup.main() == 0
    assert all("tesco" not in e for e in out2)


def test_hook_emits_empty_on_bad_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    out = _capture(monkeypatch)
    assert catchup.main() == 0
    assert out == [""]


def test_failure_path_writes_to_env_ledger_not_real_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The _dirs autouse fixture points CCCS_HOOKS_DIR at tmp_path/hooks. The bad-stdin
    # failure path must log there, NOT to the real ~/.claude/hooks/fires.jsonl. If
    # _log_failure ever drops the hooks_dir= argument, log_event falls back to
    # Path.home()/.claude/hooks and this test fails. Guard the real home with a sentinel.
    # (Preserves the env-honouring ledger-routing fix.)
    real_fires = Path.home() / ".claude" / "hooks" / "fires.jsonl"
    before = real_fires.read_text() if real_fires.is_file() else None
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    _capture(monkeypatch)
    assert catchup.main() == 0
    env_fires = tmp_path / "hooks" / "fires.jsonl"
    assert env_fires.is_file()
    assert "catchup-failed:bad-stdin" in env_fires.read_text()
    after = real_fires.read_text() if real_fires.is_file() else None
    assert after == before  # real ledger untouched


def test_hook_never_raises_on_parse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    state.scheduler_dir().mkdir(parents=True, exist_ok=True)
    registry.registry_path().write_text("[[job]\nbroken")
    monkeypatch.setattr(catchup, "_now", lambda: datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(reconcile, "spawn_detached", _Spawn())
    _stdin(monkeypatch, {"hookEventName": "SessionStart", "session_id": "u", "cwd": "/tmp"})
    out = _capture(monkeypatch)
    assert catchup.main() == 0
    assert any("failed to parse" in e for e in out)
