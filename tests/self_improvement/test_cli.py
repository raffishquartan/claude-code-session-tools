"""`ccst self-improvement` end to end, in a subprocess with the temporary environment."""
from __future__ import annotations

import subprocess
import sys

import pytest

from .conftest import Env, iso_days_ago


def ccst(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "self-improvement", *args],
        capture_output=True, text=True, check=False,
    )


def test_help_lists_every_verb() -> None:
    proc = ccst("--help")
    assert proc.returncode == 0
    for verb in ("collect-signals", "auto-review", "nudge", "status"):
        assert verb in proc.stdout


def test_a_verb_is_required() -> None:
    assert ccst().returncode == 2


def test_auto_review_help_names_the_subscription_and_cap() -> None:
    out = ccst("auto-review", "--help").stdout
    assert "subscription" in out and "0.50" in out and "--force" in out


def test_collect_signals_prints_the_summary_and_writes_the_snapshot(si: Env) -> None:
    si.write_backlog(1, 2)
    si.interactive_review(3)
    proc = ccst("collect-signals", "--no-escalate")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("self-improvement: 1 signals (inbox 1/2d, ")
    assert si.snapshot_value("SIGNAL_TOTAL") == "1"


def test_nudge_previews_without_stamping(si: Env) -> None:
    assert ccst("nudge").stdout == "no review due\n"  # loop never used
    si.write_backlog(0)
    proc = ccst("nudge")
    assert proc.returncode == 0
    assert proc.stdout.startswith("[ccst] Self-improvement review due: no review recorded")
    assert not (si.state / "last-nudge").exists()
    si.interactive_review(1)
    (si.state / "signals.env").write_text("SIGNAL_TOTAL=0\n" + "".join(
        f"{k}=0\n" for k in ("INBOX_COUNT", "BACKLOG_OPEN", "MSGS_UNCLAIMED_14D", "DENIES_7D", "DRIFT_PUSH", "RELIABILITY_OPEN")
    ))
    assert ccst("nudge").stdout == "no review due\n"


def test_status_reports_state_and_writes_nothing(si: Env) -> None:
    si.interactive_review(5)
    si.review_line(f"{iso_days_ago(2)} mode=auto status=ran sessions=1 proposals=2 cost_usd=0.12 duration_s=9")
    proc = ccst("status")
    assert proc.returncode == 0
    assert "snapshot: none" in proc.stdout
    assert "last interactive review: " + iso_days_ago(5) in proc.stdout
    assert "last headless run: " + iso_days_ago(2) in proc.stdout
    assert "1 attempts, USD 0.12 known, 0 with unknown cost" in proc.stdout
    assert sorted(p.name for p in si.state.iterdir()) == ["reviews.log"]


def test_auto_review_without_backlog_exits_0_and_logs(si: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    # No claude on PATH: a real model call is impossible even if the guards regressed.
    empty = si.root / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    proc = ccst("auto-review")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("self-improvement auto-review: ")
    assert " mode=auto " in si.last_log()
