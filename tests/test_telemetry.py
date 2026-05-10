from __future__ import annotations

import datetime
import json
import os
import stat
import threading
from pathlib import Path

import pytest

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


# ---------- TelemetryEntry schema ----------

def test_telemetry_entry_round_trips_to_json() -> None:
    entry = TelemetryEntry(
        hook="bash-security-review",
        event="PreToolUse",
        tool="Bash",
        session_id="abc-123",
        cwd_short="repos/cccs",
        decision="allow",
        cache="miss",
        verdict="safe",
        input_hash="sha256:aabbcc",
    )
    j = entry.to_json_line()
    assert '"hook":"bash-security-review"' in j
    assert '"v":1' in j
    assert j.endswith("\n")


def test_telemetry_entry_ts_is_utc_iso8601() -> None:
    entry = _make_entry()
    j = entry.to_json_line()
    data = json.loads(j)
    ts = data["ts"]
    assert ts.endswith("Z")
    datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---------- log_event: file creation and mode ----------

def test_log_event_creates_file(tmp_hooks_dir: Path) -> None:
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)
    assert (tmp_hooks_dir / "fires.jsonl").exists()


def test_log_event_file_mode_0600(tmp_hooks_dir: Path) -> None:
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)
    fires = tmp_hooks_dir / "fires.jsonl"
    mode = stat.S_IMODE(fires.stat().st_mode)
    assert mode == 0o600, f"expected 0600 got {oct(mode)}"


def test_log_event_appends_valid_jsonl(tmp_hooks_dir: Path) -> None:
    entry = _make_entry()
    log_event(entry, hooks_dir=tmp_hooks_dir)
    log_event(entry, hooks_dir=tmp_hooks_dir)
    fires = tmp_hooks_dir / "fires.jsonl"
    lines = [line for line in fires.read_text().splitlines() if line]
    assert len(lines) == 2
    for line in lines:
        data = json.loads(line)
        assert data["v"] == 1


# ---------- log_event: disk-full does not raise ----------

def test_log_event_disk_full_does_not_raise(
    tmp_hooks_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_open = os.open

    def fail_open(path: str, flags: int, mode: int = 0o666) -> int:
        if "fires.jsonl" in str(path):
            raise OSError("No space left on device")
        return original_open(path, flags, mode)

    monkeypatch.setattr(os, "open", fail_open)
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)  # must not raise


# ---------- log_event: concurrent writes ----------

def test_log_event_concurrent_writes_no_corruption(tmp_hooks_dir: Path) -> None:
    entries = [_make_entry() for _ in range(20)]
    errors: list[Exception] = []

    def write_one(e: TelemetryEntry) -> None:
        try:
            log_event(e, hooks_dir=tmp_hooks_dir)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write_one, args=(e,)) for e in entries]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    fires = tmp_hooks_dir / "fires.jsonl"
    lines = [line for line in fires.read_text().splitlines() if line]
    assert len(lines) == 20
    for line in lines:
        json.loads(line)


# ---------- log_event: rotation ----------

def test_log_event_rotates_when_over_size_limit(tmp_hooks_dir: Path) -> None:
    fires = tmp_hooks_dir / "fires.jsonl"
    fires.write_text("x" * (512 * 1024 + 1))
    fires.chmod(0o600)
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)
    rotated = list(tmp_hooks_dir.glob("fires.*.jsonl.gz"))
    assert len(rotated) == 1, f"expected 1 rotated file, got {rotated}"
    new_lines = [line for line in fires.read_text().splitlines() if line]
    assert len(new_lines) == 1


def test_rotation_file_is_valid_gzip(tmp_hooks_dir: Path) -> None:
    import gzip

    fires = tmp_hooks_dir / "fires.jsonl"
    fires.write_text("x" * (512 * 1024 + 1))
    fires.chmod(0o600)
    log_event(_make_entry(), hooks_dir=tmp_hooks_dir)
    rotated = list(tmp_hooks_dir.glob("fires.*.jsonl.gz"))
    assert rotated
    with gzip.open(rotated[0], "rb") as f:
        content = f.read()
    assert len(content) > 0


# ---------- CLI entry point ----------

def test_telemetry_cli_log_subcommand(tmp_hooks_dir: Path) -> None:
    import subprocess
    import sys

    hook_input = json.dumps({
        "session_id": "sess-1",
        "cwd": "/home/alice/repos/foo",
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
    fires = tmp_hooks_dir / "fires.jsonl"
    assert fires.exists()
    data = json.loads(fires.read_text().strip())
    assert data["hook"] == "bash-security-review"
    assert data["session_id"] == "sess-1"
    assert data["cwd"] == "repos/foo"
