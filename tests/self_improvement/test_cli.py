"""`ccst self-improvement` end to end, in a subprocess with the temporary environment."""
from __future__ import annotations

import subprocess
import sys

import pytest

from .conftest import Env, days_ago, iso_days_ago


def ccst(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "self-improvement", *args],
        capture_output=True, text=True, check=False,
    )


def test_help_lists_every_verb() -> None:
    proc = ccst("--help")
    assert proc.returncode == 0
    for verb in ("collect-signals", "auto-review", "nudge", "status", "init", "record-review"):
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


def test_init_creates_the_backlog_and_sets_up_the_loop(si: Env) -> None:
    proc = ccst("init")
    assert proc.returncode == 0, proc.stderr
    assert "created" in proc.stdout
    text = si.backlog.read_text()
    assert text.splitlines()[0] == "# Self-improvement backlog"
    assert "## Inbox" in text.splitlines() and "## Entries" in text.splitlines()
    assert "loop: set up" in ccst("status").stdout


def test_init_never_changes_an_existing_backlog(si: Env) -> None:
    si.write_backlog(2, 3, 1)
    before = si.backlog.read_bytes()
    proc = ccst("init")
    assert proc.returncode == 0
    assert "already exists" in proc.stdout
    assert si.backlog.read_bytes() == before


def test_init_exits_1_and_leaves_a_backlog_without_an_inbox(si: Env) -> None:
    si.backlog.write_text("# Mine\n\n## Entries\n")
    proc = ccst("init")
    assert proc.returncode == 1
    assert "## Inbox" in proc.stderr
    assert si.backlog.read_text() == "# Mine\n\n## Entries\n"


def test_init_does_not_follow_a_dangling_symlink(si: Env) -> None:
    target = si.root / "elsewhere" / "backlog.md"
    si.backlog.symlink_to(target)
    proc = ccst("init")
    assert proc.returncode == 1
    assert "cannot be read" in proc.stderr
    assert not target.exists()
    assert si.backlog.is_symlink()


def test_init_reports_a_directory_at_the_backlog_path_as_unreadable(si: Env) -> None:
    si.backlog.mkdir()
    proc = ccst("init")
    assert proc.returncode == 1
    assert "cannot be read" in proc.stderr
    assert si.backlog.is_dir()


def test_status_says_how_to_set_up_the_loop_and_creates_nothing(si: Env) -> None:
    proc = ccst("status")
    assert proc.returncode == 0
    first = proc.stdout.splitlines()[0]
    assert first.startswith("loop: not set up - run `ccst self-improvement init`")
    assert "interactive review: run /review-session-and-improve-claude-setup periodic" in proc.stdout
    assert not si.backlog.exists()
    assert not si.state.exists()


def test_not_set_up_job_lines_name_init_and_fit_the_digest(si: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    # The scheduler keeps the first 1000 characters of a job's stdout for the weekly digest.
    empty = si.root / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    for verb in (("collect-signals", "--no-escalate"), ("auto-review",)):
        out = ccst(*verb).stdout
        assert len(out.strip()) <= 1000
        assert "ccst self-improvement init" in out.strip().splitlines()[-1]


def test_record_review_appends_an_interactive_line(si: Env) -> None:
    si.interactive_review(40)
    proc = ccst("record-review", "--mode", "C", "--findings", "3", "--applied", "1", "--deferred", "2")
    assert proc.returncode == 0, proc.stderr
    last = si.last_log()
    assert last.startswith(days_ago(0) + "T")
    assert last.endswith(" mode=C findings=3 applied=1 deferred=2")
    assert proc.stdout == last + "\n"
    assert "last interactive review: " + last in ccst("status").stdout


def test_record_review_creates_the_state_directory(si: Env) -> None:
    assert not si.state.exists()
    proc = ccst("record-review", "--mode", "B", "--findings", "0", "--applied", "0", "--deferred", "0")
    assert proc.returncode == 0, proc.stderr
    assert si.last_log().endswith(" mode=B findings=0 applied=0 deferred=0")


@pytest.mark.parametrize("args", [
    ("--mode", "auto", "--findings", "0", "--applied", "0", "--deferred", "0"),
    ("--mode", "B", "--findings", "-1", "--applied", "0", "--deferred", "0"),
    ("--mode", "B", "--findings", "x", "--applied", "0", "--deferred", "0"),
    ("--mode", "B", "--findings", "0", "--applied", "0"),
])
def test_record_review_rejects_invalid_input_and_writes_nothing(si: Env, args: tuple[str, ...]) -> None:
    proc = ccst("record-review", *args)
    assert proc.returncode == 2
    assert not si.state.exists()
