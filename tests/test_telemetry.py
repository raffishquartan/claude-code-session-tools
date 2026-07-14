from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store
from cccs_hooks.telemetry import TelemetryEntry, log_event


# ---------- helpers ----------

def _make_entry(**overrides: object) -> TelemetryEntry:
    base = dict(
        hook="test-hook",
        event="PreToolUse",
        tool="Bash",
        session_id="s1",
        cwd_short="repos/x",
        decision="allow",
        cache="none",
        verdict="safe",
        input_hash="sha256:00",
    )
    base.update(overrides)
    return TelemetryEntry(**base)  # type: ignore[arg-type]


def _rows(hooks_dir: Path) -> list[sqlite3.Row]:
    conn = telemetry_store.connect(hooks_dir)
    try:
        return conn.execute("SELECT * FROM telemetry_events ORDER BY id").fetchall()
    finally:
        conn.close()


# ---------- log_event: row creation ----------

def test_log_event_creates_db_and_inserts_row(tmp_hooks_dir: Path) -> None:
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)
    assert (tmp_hooks_dir / "telemetry.db").exists()
    rows = _rows(tmp_hooks_dir)
    assert len(rows) == 1
    assert rows[0]["hook"] == "test-hook"
    assert rows[0]["verdict"] == "safe"


def test_log_event_ts_is_utc_iso8601(tmp_hooks_dir: Path) -> None:
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)
    ts = _rows(tmp_hooks_dir)[0]["ts"]
    assert ts.endswith("Z")
    datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_log_event_twice_inserts_two_rows(tmp_hooks_dir: Path) -> None:
    entry = _make_entry()
    log_event(entry, hooks_dir=tmp_hooks_dir)
    log_event(entry, hooks_dir=tmp_hooks_dir)
    assert len(_rows(tmp_hooks_dir)) == 2


def test_log_event_preserves_shortened_cwd(tmp_hooks_dir: Path) -> None:
    log_event(_make_entry(cwd_short="repos/cccs"), hooks_dir=tmp_hooks_dir)
    assert _rows(tmp_hooks_dir)[0]["cwd_short"] == "repos/cccs"


# ---------- log_event: never raises ----------

def test_log_event_sqlite_error_does_not_raise(
    tmp_hooks_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_connect(explicit_dir: Path | None = None) -> sqlite3.Connection:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(telemetry_store, "connect", fail_connect)
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)  # must not raise


def test_log_event_os_error_does_not_raise(
    tmp_hooks_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_connect(explicit_dir: Path | None = None) -> sqlite3.Connection:
        raise OSError("No space left on device")

    monkeypatch.setattr(telemetry_store, "connect", fail_connect)
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)  # must not raise


# ---------- log_event: concurrent writes ----------

def test_log_event_concurrent_writes_no_corruption(tmp_hooks_dir: Path) -> None:
    entries = [_make_entry() for _ in range(20)]
    errors: list[Exception] = []

    def write_one(e: TelemetryEntry) -> None:
        try:
            log_event(e, hooks_dir=tmp_hooks_dir)
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=write_one, args=(e,)) for e in entries]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(_rows(tmp_hooks_dir)) == 20


# ---------- CLI entry point ----------

def test_telemetry_cli_log_subcommand(tmp_hooks_dir: Path) -> None:
    import subprocess
    import sys

    hook_input = json.dumps({
        "session_id": "sess-1",
        "cwd": "/example/repos/foo",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    })
    env = {**os.environ, "CCCS_HOOKS_DIR": str(tmp_hooks_dir)}
    result = subprocess.run(
        [
            sys.executable, "-m", "cccs_hooks.telemetry", "log",
            "--hook", "bash-security-review",
            "--event", "PreToolUse",
            "--decision", "allow",
            "--cache", "miss",
            "--verdict", "safe",
            "--input-hash", "sha256:ab",
        ],
        input=hook_input,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).parent.parent),
    )
    assert result.returncode == 0, result.stderr
    rows = _rows(tmp_hooks_dir)
    assert len(rows) == 1
    assert rows[0]["hook"] == "bash-security-review"
    assert rows[0]["session_id"] == "sess-1"
    assert rows[0]["cwd_short"] == "repos/foo"
