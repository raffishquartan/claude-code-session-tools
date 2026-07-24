"""Tests for ccst telemetry trim and cccs_hooks.telemetry_trim module."""
from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store
from cccs_hooks.telemetry_trim import enforce_max_size, trim, trim_by_age


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


def _days_ago(n: int) -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_event(hooks_dir: Path, *, ts: str, verdict: str = "safe") -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO telemetry_events "
        "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
        "VALUES (?, 'test-hook', 'PreToolUse', 'Bash', 's1', 'x', 'allow', 'none', ?, '')",
        (ts, verdict),
    )
    conn.commit()
    conn.close()


def _insert_catchup(hooks_dir: Path, *, ts: str) -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO catchup_events "
        "(ts, job_id, event, owed, ran, exit_code, duration_ms, error, consecutive_failures) "
        "VALUES (?, 'job', 'run', 1, 1, 0, 1, NULL, 0)",
        (ts,),
    )
    conn.commit()
    conn.close()


# ---------- trim_by_age ----------

def test_trim_by_age_removes_old_rows_from_both_tables(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(10))
    _insert_event(tmp_path, ts=_days_ago(1))
    _insert_catchup(tmp_path, ts=_days_ago(10))
    _insert_catchup(tmp_path, ts=_days_ago(1))
    conn = telemetry_store.connect(tmp_path)
    kept, removed = trim_by_age(conn, max_age_days=5)
    conn.close()
    assert kept == 2
    assert removed == 2


def test_trim_by_age_keeps_all_recent_rows(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(1))
    conn = telemetry_store.connect(tmp_path)
    kept, removed = trim_by_age(conn, max_age_days=5)
    conn.close()
    assert kept == 1
    assert removed == 0


def test_trim_by_age_no_rows_returns_zero(tmp_path: Path) -> None:
    conn = telemetry_store.connect(tmp_path)
    kept, removed = trim_by_age(conn, max_age_days=5)
    conn.close()
    assert kept == 0
    assert removed == 0


# ---------- enforce_max_size ----------

def test_enforce_max_size_below_threshold_deletes_nothing(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(1))
    conn = telemetry_store.connect(tmp_path)
    db_path = telemetry_store.db_path(tmp_path)
    removed = enforce_max_size(conn, db_path, max_size_mb=10.0)
    conn.close()
    assert removed == 0


def test_enforce_max_size_deletes_oldest_rows_until_under_threshold(tmp_path: Path) -> None:
    conn = telemetry_store.connect(tmp_path)
    db_path = telemetry_store.db_path(tmp_path)
    for i in range(500):
        conn.execute(
            "INSERT INTO telemetry_events "
            "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
            "VALUES (?, 'test-hook', 'PreToolUse', 'Bash', 's1', 'x', 'allow', 'none', ?, '')",
            (_days_ago(500 - i), "x" * 500),  # inflate row size
        )
    conn.commit()
    telemetry_store.checkpoint_and_vacuum(conn)
    before_size = db_path.stat().st_size
    removed = enforce_max_size(conn, db_path, max_size_mb=0.05)
    after_size = db_path.stat().st_size
    remaining = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert removed > 0
    assert after_size < before_size
    assert after_size <= 0.05 * 1024 * 1024 or remaining == 0


# ---------- trim() high-level ----------

def test_trim_age_and_size_combined(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(20))
    _insert_event(tmp_path, ts=_days_ago(1))
    result = trim(max_size_mb=100.0, max_age_days=5, hooks_dir=tmp_path)
    assert result["rows_removed_by_age"] == 1
    assert result["rows_kept_after_age"] == 1
    assert result["rows_removed_by_size"] == 0


def test_trim_dry_run_does_not_modify(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(10))
    _insert_event(tmp_path, ts=_days_ago(1))
    before = telemetry_store.db_path(tmp_path).stat().st_size
    trim(max_age_days=5, hooks_dir=tmp_path, dry_run=True)
    after = telemetry_store.db_path(tmp_path).stat().st_size
    conn = telemetry_store.connect(tmp_path)
    count = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert count == 2
    assert before == after


def test_trim_dry_run_reports_would_remove(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(10))
    _insert_event(tmp_path, ts=_days_ago(1))
    result = trim(max_age_days=5, hooks_dir=tmp_path, dry_run=True)
    assert result.get("would_remove_by_age") == 1


def test_trim_no_db_returns_summary(tmp_path: Path) -> None:
    result = trim(max_age_days=5, hooks_dir=tmp_path)
    assert result["rows_removed_by_age"] == 0


# ---------- CLI integration ----------

def test_telemetry_trim_no_flags_exits_ok(tmp_path: Path) -> None:
    telemetry_store.connect(tmp_path).close()
    result = _run("telemetry", "trim", "--hooks-dir", str(tmp_path))
    assert result.returncode == 0


def test_telemetry_trim_max_age_days(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(20))
    _insert_event(tmp_path, ts=_days_ago(1))
    result = _run("telemetry", "trim", "--hooks-dir", str(tmp_path), "--max-age-days", "5")
    assert result.returncode == 0
    conn = telemetry_store.connect(tmp_path)
    count = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert count == 1


def test_telemetry_trim_dry_run(tmp_path: Path) -> None:
    _insert_event(tmp_path, ts=_days_ago(20))
    result = _run(
        "telemetry", "trim", "--hooks-dir", str(tmp_path), "--max-age-days", "5", "--dry-run",
    )
    assert result.returncode == 0
    assert "Dry run" in result.stdout
    conn = telemetry_store.connect(tmp_path)
    count = conn.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    conn.close()
    assert count == 1
