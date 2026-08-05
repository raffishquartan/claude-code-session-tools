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


# ---------- advance_all_cursors_to ----------


def test_advance_all_cursors_moves_every_stale_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    cursor.write_cursor("a", 3)
    cursor.write_cursor("b", 7)
    moved = cursor.advance_all_cursors_to(20)
    assert moved == 2
    assert cursor.read_cursor("a") == 20
    assert cursor.read_cursor("b") == 20


def test_advance_all_cursors_never_moves_a_cursor_backwards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    cursor.write_cursor("ahead", 99)
    cursor.write_cursor("behind", 2)
    moved = cursor.advance_all_cursors_to(50)
    assert moved == 1
    assert cursor.read_cursor("ahead") == 99
    assert cursor.read_cursor("behind") == 50


def test_advance_all_cursors_is_a_no_op_without_a_scheduler_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "nonexistent"))
    assert cursor.advance_all_cursors_to(20) == 0
    assert not (tmp_path / "nonexistent" / "ccsched.db").exists()
