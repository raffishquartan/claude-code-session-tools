# tests/scheduler/test_catchup_hook.py
from __future__ import annotations

import io
import json
import sqlite3
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


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture the event name passed to _emit (the value echoed back to Claude
    as hookSpecificOutput.hookEventName)."""
    events: list[str] = []
    monkeypatch.setattr(catchup, "_emit", lambda ctx, event: events.append(event))
    return events


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
    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u", "cwd": "/tmp"})
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
    monkeypatch.setattr(catchup, "_now", lambda: datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc))
    # Avoid launching anything real on this UserPromptSubmit reap.
    monkeypatch.setattr(reconcile, "spawn_detached", _Spawn())
    # First-ever catchup call for this session seeds its cursor at the current end of
    # the ledger and sees nothing yet - establishing that baseline is exactly what stops
    # a later-created backlog from replaying as if it just happened (see
    # test_new_session_seed_skips_pre_existing_backlog in test_surface.py).
    _stdin(monkeypatch, {"hook_event_name": "UserPromptSubmit", "session_id": "u", "cwd": "/tmp"})
    out0 = _capture(monkeypatch)
    assert catchup.main() == 0
    assert all("tesco" not in e for e in out0)
    # A worker completes a run after this session's baseline was established.
    ledger.record(ledger.LedgerEntry(
        job_id="tesco", event=ledger.LedgerEvent.RUN, owed=1, ran=1,
        exit_code=0, duration_ms=1, error=None))
    out = _capture(monkeypatch)
    _stdin(monkeypatch, {"hook_event_name": "UserPromptSubmit", "session_id": "u", "cwd": "/tmp"})
    assert catchup.main() == 0
    assert any("tesco" in e for e in out)
    # Surfaced once: a second reap for the same session sees nothing new.
    out2 = _capture(monkeypatch)
    _stdin(monkeypatch, {"hook_event_name": "UserPromptSubmit", "session_id": "u", "cwd": "/tmp"})
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
    # failure path must log there, NOT to the real ~/.local/share/claude/telemetry.db. If
    # _log_failure ever drops the hooks_dir= argument, log_event falls back to
    # paths.data_home() and this test fails. Guard the real home with a sentinel.
    real_db = Path.home() / ".local" / "share" / "claude" / "telemetry.db"
    before_mtime = real_db.stat().st_mtime if real_db.is_file() else None
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    _capture(monkeypatch)
    assert catchup.main() == 0
    env_db = tmp_path / "hooks" / "telemetry.db"
    assert env_db.is_file()
    conn = sqlite3.connect(str(env_db))
    row = conn.execute(
        "SELECT verdict FROM telemetry_events WHERE hook = 'catchup'"
    ).fetchone()
    conn.close()
    assert row is not None and "catchup-failed:bad-stdin" in row[0]
    after_mtime = real_db.stat().st_mtime if real_db.is_file() else None
    assert after_mtime == before_mtime  # real telemetry.db untouched


def test_hook_never_raises_on_corrupt_db(monkeypatch: pytest.MonkeyPatch) -> None:
    # Post-consolidation, registry/cursor/state/throttle all share one ccsched.db.
    # main() calls cursor.seed_new_session(uuid) BEFORE reconcile, so a corrupt DB
    # makes store.connect() inside seed_new_session raise sqlite3.DatabaseError,
    # caught by main()'s top-level `except (..., sqlite3.Error)` guard BEFORE
    # reconcile's parse_error digest path is reached. The correct observable
    # behaviour is therefore an empty degrade, not a "failed to load" digest.
    from cc_session_tools.lib.scheduler import store
    store.scheduler_dir().mkdir(parents=True, exist_ok=True)
    store.db_path().write_bytes(b"not a sqlite db")
    monkeypatch.setattr(catchup, "_now", lambda: datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(reconcile, "spawn_detached", _Spawn())
    _stdin(monkeypatch, {"hook_event_name": "SessionStart", "session_id": "u", "cwd": "/tmp"})
    out = _capture(monkeypatch)
    assert catchup.main() == 0  # never raises, never blocks a session — the §15 invariant
    assert out == [""]  # empty degrade, not a digest string


@pytest.mark.parametrize("event", ["SessionStart", "UserPromptSubmit"])
def test_emits_event_name_matching_invoking_event(
    event: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: Claude Code rejects a hook whose hookSpecificOutput.hookEventName
    does not match the invoking event. The event must be read from the stdin
    `hook_event_name` field (snake_case), not `hookEventName` (camelCase), which
    is absent on input and would silently default to SessionStart - making every
    UserPromptSubmit invocation fail with 'expected UserPromptSubmit but got
    SessionStart'."""
    monkeypatch.setattr(catchup, "_now", lambda: datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(reconcile, "spawn_detached", _Spawn())
    _stdin(monkeypatch, {"hook_event_name": event, "session_id": "u", "cwd": "/tmp"})
    events = _capture_events(monkeypatch)
    assert catchup.main() == 0
    assert events == [event]
