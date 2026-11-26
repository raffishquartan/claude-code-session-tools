"""Port of the config repository's nudge bats suite, plus the dedupe and preview cases."""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable
import io
import json
import subprocess
import sys

import pytest

from cc_session_tools.lib.self_improvement import nudge
from hooks import self_improvement_nudge

from .conftest import Env, iso_days_ago, tree_checksums


def write_snapshot(si: Env, inbox: int, total: int, msgs: int = 0) -> None:
    si.state.mkdir(parents=True, exist_ok=True)
    (si.state / "signals.env").write_text(
        f"COLLECTED_AT={iso_days_ago(1)}\nINBOX_COUNT={inbox}\nINBOX_OLDEST_DAYS=3\n"
        f"BACKLOG_OPEN=0\nMSGS_UNCLAIMED_14D={msgs}\nDENIES_7D=0\nDRIFT_PUSH=0\n"
        f"RELIABILITY_OPEN=0\nSIGNAL_TOTAL={total}\nDAYS_SINCE_REVIEW=1\nLOOP_HEALTH=PASS\n"
        "AUTO_REVIEW_HEALTH=PASS\n",
        encoding="utf-8",
    )


def replace_key(si: Env, key: str, value: str) -> None:
    path = si.state / "signals.env"
    lines = [
        f"{key}={value}" if line.startswith(f"{key}=") else line
        for line in path.read_text().splitlines()
    ]
    path.write_text("\n".join(lines) + "\n")


def run_hook(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"hook_event_name": "SessionStart"}'))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    assert self_improvement_nudge.main() == 0
    return out.getvalue()


def system_message(output: str) -> str:
    data = json.loads(output)
    assert data["hookSpecificOutput"] == {
        "hookEventName": "SessionStart", "additionalContext": data["systemMessage"],
    }
    return str(data["systemMessage"])


@pytest.fixture
def hook(si: Env, monkeypatch: pytest.MonkeyPatch) -> Callable[[], str]:
    si.state.mkdir(parents=True, exist_ok=True)
    return lambda: run_hook(monkeypatch)


def test_nudges_when_review_14_plus_days_old_and_a_signal_is_non_zero(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(20)
    write_snapshot(si, 3, 3)
    msg = system_message(hook())
    assert msg == (
        "[ccst] Self-improvement review due: last review 20 days ago, 3 signals (inbox 3, "
        "entries 0, msgs 0, denies 0, drift 0, reliability 0) - run "
        "/review-session-and-improve-claude-setup periodic"
    )


def test_nudges_when_signal_total_is_at_least_10(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(5)
    write_snapshot(si, 0, 10, 10)
    assert "10 signals" in system_message(hook())


def test_nudges_at_30_days_even_with_no_signals(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(30)
    write_snapshot(si, 0, 0)
    assert "last review 30 days ago" in system_message(hook())


@pytest.mark.parametrize(("age", "inbox", "total"), [(5, 4, 4), (14, 0, 0)])
def test_silent_when_nothing_is_due(si: Env, hook: Callable[[], str], age: int, inbox: int, total: int) -> None:
    si.interactive_review(age)
    write_snapshot(si, inbox, total)
    assert hook() == ""


def test_silent_when_already_nudged_today_and_first_nudge_writes_stamp(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(20)
    write_snapshot(si, 3, 3)
    assert "Self-improvement review due" in system_message(hook())
    assert (si.state / "last-nudge").read_text() == f"{dt.date.today().isoformat()}\n"
    assert hook() == ""


def test_nudges_again_when_last_nudge_was_yesterday(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(20)
    write_snapshot(si, 3, 3)
    (si.state / "last-nudge").write_text(f"{dt.date.today() - dt.timedelta(days=1)}\n")
    assert "Self-improvement review due" in system_message(hook())


def test_silent_when_snapshot_missing_and_review_recent(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(10)
    assert hook() == ""


def test_signals_unknown_when_snapshot_missing_and_review_overdue(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(31)
    msg = system_message(hook())
    assert "last review 31 days ago" in msg
    assert "signals unknown" in msg


def test_no_review_recorded_when_only_the_backlog_exists(si: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    si.write_backlog(0)
    msg = system_message(run_hook(monkeypatch))
    assert "no review recorded" in msg
    assert "signals unknown" in msg


def test_silent_on_an_install_that_never_used_the_loop(si: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_hook(monkeypatch) == ""
    assert not si.state.exists()


def test_malformed_snapshot_is_missing_when_review_recent(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(10)
    (si.state / "signals.env").write_text("SIGNAL_TOTAL=lots\nINBOX_COUNT=$(rm -rf /)\n")
    assert hook() == ""


def test_malformed_snapshot_is_missing_when_overdue(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(40)
    (si.state / "signals.env").write_text("garbage without equals\n")
    assert "signals unknown" in system_message(hook())


def test_component_with_unexpected_text_makes_snapshot_malformed(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(40)
    write_snapshot(si, 3, 3)
    replace_key(si, "MSGS_UNCLAIMED_14D", "Legal matter X")
    msg = system_message(hook())
    assert "signals unknown" in msg
    assert "Legal" not in msg


def test_only_auto_lines_since_interactive_review(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(20)
    si.review_line(
        f"{iso_days_ago(1)} mode=auto status=ran sessions=3 proposals=2 cost_usd=0.11 duration_s=40"
    )
    write_snapshot(si, 3, 3)
    assert "last review 20 days ago" in system_message(hook())


def test_unavailable_components_are_shown(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(20)
    write_snapshot(si, 2, 2)
    replace_key(si, "MSGS_UNCLAIMED_14D", "unavailable")
    assert "msgs unavailable" in system_message(hook())


def test_writes_nothing_outside_the_state_directory(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(20)
    write_snapshot(si, 3, 3)
    before = tree_checksums(si.root, [si.state])
    hook()
    assert tree_checksums(si.root, [si.state]) == before


@pytest.mark.parametrize(
    "command",
    [
        "bash $HOME/repos/config/hooks/session-start/self-improvement-nudge.sh",
        "'/opt/config/hooks/session-start/self-improvement-nudge.sh'",
    ],
)
def test_silent_when_the_bash_nudge_is_installed(si: Env, hook: Callable[[], str], command: str) -> None:
    si.interactive_review(40)
    settings = {
        "hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "ccst hooks run session-tag"},
            {"type": "command", "command": command},
        ]}]}
    }
    (si.home / ".claude" / "settings.json").write_text(json.dumps(settings))
    assert hook() == ""
    assert not (si.state / "last-nudge").exists()


def test_a_bash_nudge_under_another_event_does_not_silence(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(40)
    settings = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "bash /x/self-improvement-nudge.sh"},
    ]}]}}
    (si.home / ".claude" / "settings.json").write_text(json.dumps(settings))
    assert "Self-improvement review due" in system_message(hook())


def test_unparseable_settings_do_not_break_the_hook(si: Env, hook: Callable[[], str]) -> None:
    si.interactive_review(40)
    (si.home / ".claude" / "settings.json").write_text("{not json")
    assert "Self-improvement review due" in system_message(hook())


def test_unreadable_review_log_counts_as_no_review(si: Env, hook: Callable[[], str]) -> None:
    (si.state / "reviews.log").mkdir()
    assert "no review recorded" in system_message(hook())


def test_undecodable_review_log_bytes_are_tolerated(si: Env, hook: Callable[[], str]) -> None:
    (si.state / "reviews.log").write_bytes(
        f"{iso_days_ago(20)} mode=C findings=0 applied=0 deferred=0\n\xff\xfe\n".encode("latin-1")
    )
    write_snapshot(si, 3, 3)
    assert "last review 20 days ago" in system_message(hook())


def test_an_unexpected_error_exits_0_silently(
    si: Env, hook: Callable[[], str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_: object) -> str:
        raise ValueError("unexpected")

    monkeypatch.setattr(nudge, "session_start_message", boom)
    assert hook() == ""


def test_a_stamp_that_cannot_be_written_still_nudges(
    si: Env, hook: Callable[[], str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cc_session_tools.lib.self_improvement import state

    def fail(*_: object) -> None:
        raise OSError("read-only")

    si.interactive_review(20)
    write_snapshot(si, 3, 3)
    monkeypatch.setattr(state, "atomic_write", fail)
    assert "Self-improvement review due" in system_message(hook())


@pytest.mark.parametrize(("age", "due"), [(13, False), (14, True)])
def test_age_with_signal_boundary(si: Env, hook: Callable[[], str], age: int, due: bool) -> None:
    si.interactive_review(age)
    write_snapshot(si, 1, 1)
    assert (hook() != "") is due


def test_a_future_dated_review_counts_as_no_review(si: Env, hook: Callable[[], str]) -> None:
    si.review_line(f"{iso_days_ago(-3)} mode=C findings=0 applied=0 deferred=0")
    write_snapshot(si, 0, 0)
    assert "no review recorded" in system_message(hook())


def test_preview_does_not_consume_the_daily_nudge(si: Env) -> None:
    si.interactive_review(20)
    write_snapshot(si, 3, 3)
    text = nudge.due_message(si.state)
    assert text is not None and text.startswith("[ccst] Self-improvement review due")
    assert not (si.state / "last-nudge").exists()


def test_hook_runs_through_the_ccst_dispatcher(si: Env) -> None:
    si.interactive_review(40)
    proc = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "hooks", "run", "self-improvement-nudge"],
        input="{}", capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Self-improvement review due" in system_message(proc.stdout)
