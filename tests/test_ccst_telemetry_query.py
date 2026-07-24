from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store
from cccs_hooks.telemetry_query import query_events


def _insert(
    hooks_dir: Path, *, ts: str, hook: str = "bash-security-review",
    decision: str = "allow", verdict: str = "safe",
) -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO telemetry_events "
        "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
        "VALUES (?, ?, 'PreToolUse', 'Bash', 's1', 'x', ?, 'none', ?, '')",
        (ts, hook, decision, verdict),
    )
    conn.commit()
    conn.close()


def test_query_events_filters_by_hook(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2026-07-01T00:00:00Z", hook="bash-security-review")
    _insert(tmp_path, ts="2026-07-01T00:00:01Z", hook="bash-hard-deny")
    rows = query_events(hook="bash-hard-deny", hooks_dir=tmp_path)
    assert [r["hook"] for r in rows] == ["bash-hard-deny"]


def test_query_events_filters_by_decision(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2026-07-01T00:00:00Z", decision="allow")
    _insert(tmp_path, ts="2026-07-01T00:00:01Z", decision="deny")
    rows = query_events(decision="deny", hooks_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0]["decision"] == "deny"


def test_query_events_filters_by_since(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2020-01-01T00:00:00Z")
    _insert(tmp_path, ts="2099-01-01T00:00:00Z")
    rows = query_events(since_ts="2050-01-01T00:00:00Z", hooks_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0]["ts"] == "2099-01-01T00:00:00Z"


def test_query_events_orders_newest_first(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2026-07-01T00:00:00Z")
    _insert(tmp_path, ts="2026-07-02T00:00:00Z")
    rows = query_events(hooks_dir=tmp_path)
    assert [r["ts"] for r in rows] == ["2026-07-02T00:00:00Z", "2026-07-01T00:00:00Z"]


def test_query_events_respects_limit(tmp_path: Path) -> None:
    for i in range(5):
        _insert(tmp_path, ts=f"2026-07-0{i+1}T00:00:00Z")
    rows = query_events(limit=2, hooks_dir=tmp_path)
    assert len(rows) == 2


# ---------- CLI integration ----------

def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


def test_cli_query_no_events_prints_message(tmp_path: Path) -> None:
    telemetry_store.connect(tmp_path).close()
    result = _run("telemetry", "query", "--hooks-dir", str(tmp_path))
    assert result.returncode == 0
    assert "no matching" in result.stdout.lower()


def test_cli_query_prints_one_line_per_event(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2026-07-01T00:00:00Z", hook="bash-security-review", verdict="safe")
    result = _run("telemetry", "query", "--hooks-dir", str(tmp_path))
    assert result.returncode == 0
    assert "bash-security-review" in result.stdout
    assert "safe" in result.stdout


def test_cli_query_hook_filter(tmp_path: Path) -> None:
    _insert(tmp_path, ts="2026-07-01T00:00:00Z", hook="bash-hard-deny")
    _insert(tmp_path, ts="2026-07-01T00:00:01Z", hook="bash-security-review")
    result = _run("telemetry", "query", "--hooks-dir", str(tmp_path), "--hook", "bash-hard-deny")
    assert "bash-hard-deny" in result.stdout
    assert "bash-security-review" not in result.stdout


def test_cli_query_invalid_decision_rejected(tmp_path: Path) -> None:
    result = _run(
        "telemetry", "query", "--hooks-dir", str(tmp_path), "--decision", "not-a-decision",
    )
    assert result.returncode != 0


def test_cli_query_invalid_since_rejected(tmp_path: Path) -> None:
    result = _run("telemetry", "query", "--hooks-dir", str(tmp_path), "--since", "bogus")
    assert result.returncode != 0
