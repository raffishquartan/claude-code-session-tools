"""Tests for ccst telemetry trim and cccs_hooks.telemetry_trim module."""
from __future__ import annotations

import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cccs_hooks.telemetry_trim import (
    _fires_path,
    rotate_by_size,
    trim,
    trim_by_age,
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


def _make_fires(hooks_dir: Path, lines: list[dict]) -> Path:
    fires = hooks_dir / "fires.jsonl"
    fires.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return fires


def _entry(ts: str) -> dict:
    return {
        "v": 1,
        "ts": ts,
        "hook": "test-hook",
        "event": "PreToolUse",
        "tool": "Bash",
        "session_id": "s1",
        "decision": "allow",
    }


def _days_ago(n: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- _fires_path ----------

def test_fires_path_uses_hooks_dir(tmp_path: Path) -> None:
    assert _fires_path(tmp_path) == tmp_path / "fires.jsonl"


def test_fires_path_defaults_to_cache_logs(tmp_path: Path) -> None:
    p = _fires_path(None)
    assert p.name == "fires.jsonl"
    assert p.parent == Path.home() / ".cache" / "claude" / "logs"


# ---------- trim_by_age ----------

def test_trim_by_age_removes_old_lines(tmp_path: Path) -> None:
    fires = _make_fires(tmp_path, [
        _entry(_days_ago(10)),
        _entry(_days_ago(3)),
        _entry(_days_ago(1)),
    ])
    kept, removed = trim_by_age(fires, max_age_days=5)
    assert kept == 2
    assert removed == 1


def test_trim_by_age_keeps_all_recent_lines(tmp_path: Path) -> None:
    fires = _make_fires(tmp_path, [
        _entry(_days_ago(1)),
        _entry(_days_ago(2)),
    ])
    kept, removed = trim_by_age(fires, max_age_days=5)
    assert kept == 2
    assert removed == 0


def test_trim_by_age_removes_all_old_lines(tmp_path: Path) -> None:
    fires = _make_fires(tmp_path, [
        _entry(_days_ago(20)),
        _entry(_days_ago(15)),
    ])
    kept, removed = trim_by_age(fires, max_age_days=5)
    assert kept == 0
    assert removed == 2


def test_trim_by_age_no_file_returns_zero(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    kept, removed = trim_by_age(fires, max_age_days=5)
    assert kept == 0
    assert removed == 0


def test_trim_by_age_keeps_malformed_lines(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text('not json at all\n{"ts": "' + _days_ago(1) + '"}\n')
    kept, removed = trim_by_age(fires, max_age_days=5)
    assert kept == 2
    assert removed == 0


def test_trim_by_age_rewrites_file(tmp_path: Path) -> None:
    fires = _make_fires(tmp_path, [
        _entry(_days_ago(10)),
        _entry(_days_ago(1)),
    ])
    trim_by_age(fires, max_age_days=5)
    lines = [l for l in fires.read_text().splitlines() if l]
    assert len(lines) == 1


# ---------- rotate_by_size ----------

def test_rotate_by_size_below_threshold_no_rotation(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text("small content\n")
    rotated = rotate_by_size(fires, max_size_mb=10.0)
    assert not rotated
    assert fires.exists()
    assert not (tmp_path / "fires.jsonl.1").exists()


def test_rotate_by_size_creates_slot_1(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text("x" * 1024)
    rotated = rotate_by_size(fires, max_size_mb=0.0001)  # 0.1 KB threshold
    assert rotated
    assert (tmp_path / "fires.jsonl.1").exists()
    assert not fires.exists()


def test_rotate_by_size_shifts_existing_slots(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    slot1 = tmp_path / "fires.jsonl.1"
    slot1.write_text("slot1")
    fires.write_text("x" * 1024)
    rotate_by_size(fires, max_size_mb=0.0001)
    assert (tmp_path / "fires.jsonl.1").read_text() == "x" * 1024
    assert (tmp_path / "fires.jsonl.2").read_text() == "slot1"


def test_rotate_by_size_drops_oldest_slot(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    for i in range(1, 4):
        (tmp_path / f"fires.jsonl.{i}").write_text(f"slot{i}")
    fires.write_text("x" * 1024)
    rotate_by_size(fires, max_size_mb=0.0001, keep=3)
    # slot 4 should not exist (dropped)
    assert not (tmp_path / "fires.jsonl.4").exists()
    # slot 3 should now exist and be the old slot 2
    assert (tmp_path / "fires.jsonl.3").read_text() == "slot2"


def test_rotate_by_size_no_file_returns_false(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    assert not rotate_by_size(fires, max_size_mb=1.0)


# ---------- trim() high-level ----------

def test_trim_age_and_size_combined(tmp_path: Path) -> None:
    fires = _make_fires(tmp_path, [
        _entry(_days_ago(20)),
        _entry(_days_ago(1)),
    ])
    # First do age trim, leaving 1 line; then rotation shouldn't fire (file too small)
    result = trim(max_size_mb=100.0, max_age_days=5, hooks_dir=tmp_path)
    assert result["lines_removed"] == 1
    assert result["lines_kept"] == 1
    assert not result["rotated"]


def test_trim_dry_run_does_not_modify(tmp_path: Path) -> None:
    fires = _make_fires(tmp_path, [_entry(_days_ago(10)), _entry(_days_ago(1))])
    original = fires.read_text()
    trim(max_age_days=5, hooks_dir=tmp_path, dry_run=True)
    assert fires.read_text() == original


def test_trim_dry_run_reports_would_remove(tmp_path: Path) -> None:
    _make_fires(tmp_path, [_entry(_days_ago(10)), _entry(_days_ago(1))])
    result = trim(max_age_days=5, hooks_dir=tmp_path, dry_run=True)
    assert result.get("would_remove_lines") == 1


def test_trim_dry_run_reports_would_rotate(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text("x" * 1024)
    result = trim(max_size_mb=0.0001, hooks_dir=tmp_path, dry_run=True)
    assert result.get("would_rotate") is True


def test_trim_no_file_returns_summary(tmp_path: Path) -> None:
    result = trim(max_age_days=5, hooks_dir=tmp_path)
    assert not result["exists"]


# ---------- CLI integration ----------

def test_telemetry_trim_no_flags_exits_ok(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text("{}\n")
    result = _run("telemetry", "trim", "--hooks-dir", str(tmp_path))
    assert result.returncode == 0


def test_telemetry_trim_max_age_days(tmp_path: Path) -> None:
    fires = _make_fires(tmp_path, [
        _entry(_days_ago(20)),
        _entry(_days_ago(1)),
    ])
    result = _run("telemetry", "trim", "--hooks-dir", str(tmp_path), "--max-age-days", "5")
    assert result.returncode == 0
    lines = [l for l in fires.read_text().splitlines() if l]
    assert len(lines) == 1


def test_telemetry_trim_dry_run(tmp_path: Path) -> None:
    fires = _make_fires(tmp_path, [_entry(_days_ago(20)), _entry(_days_ago(1))])
    original = fires.read_text()
    result = _run("telemetry", "trim", "--hooks-dir", str(tmp_path), "--max-age-days", "5", "--dry-run")
    assert result.returncode == 0
    assert "Dry run" in result.stdout
    assert fires.read_text() == original


def test_telemetry_trim_no_file_exits_ok(tmp_path: Path) -> None:
    result = _run("telemetry", "trim", "--hooks-dir", str(tmp_path), "--max-age-days", "5")
    assert result.returncode == 0
    assert "No telemetry" in result.stdout


def test_telemetry_trim_max_size(tmp_path: Path) -> None:
    fires = tmp_path / "fires.jsonl"
    fires.write_text("x" * (2 * 1024 * 1024))  # 2 MB
    result = _run("telemetry", "trim", "--hooks-dir", str(tmp_path), "--max-size", "1")
    assert result.returncode == 0
    # Rotated
    assert (tmp_path / "fires.jsonl.1").exists()
