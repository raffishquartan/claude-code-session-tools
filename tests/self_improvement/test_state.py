from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

import pytest

from cc_session_tools.lib.self_improvement import state
from .conftest import Env, days_ago, iso_days_ago


def test_state_dir_follows_data_home(si: Env) -> None:
    assert state.state_dir() == si.state


@pytest.mark.parametrize("text", ["2026-02-31", "2026-13-01", "26-01-01", "2026-1-01", "x"])
def test_parse_date_rejects_invalid(text: str) -> None:
    assert state.parse_date(text) is None


def test_parse_date_accepts_valid() -> None:
    assert state.parse_date("2024-02-29") == dt.date(2024, 2, 29)


def test_days_since_counts_utc_days() -> None:
    assert state.days_since(dt.date.fromisoformat(days_ago(5))) == 5


def test_interactive_review_ignores_auto_lines() -> None:
    lines = [
        f"{iso_days_ago(20)} mode=C findings=2 applied=1 deferred=1",
        f"{iso_days_ago(1)} mode=auto status=ran sessions=1 proposals=0 cost_usd=0.1 duration_s=3",
    ]
    assert state.last_interactive_review_date(lines) == dt.date.fromisoformat(days_ago(20))


def test_mode_must_be_a_whole_token() -> None:
    assert state.last_interactive_review_date([f"{iso_days_ago(1)} mode=Bx findings=0"]) is None
    assert state.last_interactive_review_date([f"{iso_days_ago(1)} mode=B"]) is not None


@pytest.mark.parametrize(
    ("line", "paid"),
    [
        ("status=ran sessions=1 proposals=0 cost_usd=0.1", True),
        ("status=over-budget sessions=1 proposals=0 cost_usd=0.6", True),
        ("status=failed sessions=1 proposals=0 cost_usd=0.3", True),
        ("status=failed sessions=1 proposals=0 cost_usd=unknown", True),
        ("status=failed sessions=1 proposals=0 cost_usd=0", False),
        ("status=skipped sessions=0 proposals=0 cost_usd=0", False),
    ],
)
def test_last_paid_run_date(line: str, paid: bool) -> None:
    lines = [f"{iso_days_ago(2)} mode=auto {line} duration_s=1"]
    expected = dt.date.fromisoformat(days_ago(2)) if paid else None
    assert state.last_paid_run_date(lines) == expected


def test_headless_cost_resets_at_each_interactive_review() -> None:
    lines = [
        f"{iso_days_ago(30)} mode=auto status=ran sessions=1 proposals=0 cost_usd=0.40 duration_s=1",
        f"{iso_days_ago(20)} mode=B findings=0 applied=0 deferred=0",
        f"{iso_days_ago(10)} mode=auto status=ran sessions=1 proposals=0 cost_usd=0.10 duration_s=1",
        f"{iso_days_ago(3)} mode=auto status=failed sessions=1 proposals=0 cost_usd=unknown duration_s=1",
    ]
    assert state.headless_cost_since_last_review(lines) == (2, pytest.approx(0.10), 1)


def test_atomic_write_keeps_mode_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_text("old")
    target.chmod(0o640)
    state.atomic_write(target, "new")
    assert target.read_text() == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["f"]


def test_atomic_write_failure_leaves_target_and_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "missing-dir" / "f"
    with pytest.raises(OSError):
        state.atomic_write(target, "x")
    assert not target.parent.exists()


def test_parse_env_never_executes_and_last_wins() -> None:
    assert state.parse_env("A=1\nA=$(rm -rf /)\ngarbage\n") == {"A": "$(rm -rf /)"}
