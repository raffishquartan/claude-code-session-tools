from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import cursor


def test_missing_cursor_defaults_to_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert cursor.read_cursor("session-uuid") == 0


def test_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    cursor.write_cursor("session-uuid", 12)
    assert cursor.read_cursor("session-uuid") == 12


def test_write_cursor_is_idempotent_upsert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    cursor.write_cursor("s", 3)
    cursor.write_cursor("s", 9)
    assert cursor.read_cursor("s") == 9


def test_cursors_are_per_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    cursor.write_cursor("a", 3)
    cursor.write_cursor("b", 7)
    assert cursor.read_cursor("a") == 3
    assert cursor.read_cursor("b") == 7


def test_seed_new_session_only_seeds_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))
    cursor.seed_new_session("u")          # ledger empty -> seeds 0
    cursor.write_cursor("u", 5)           # advance
    cursor.seed_new_session("u")          # must NOT reseed back to 0
    assert cursor.read_cursor("u") == 5
