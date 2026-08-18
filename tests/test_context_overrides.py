import sqlite3
from pathlib import Path

from cc_session_tools.lib import context_overrides, sessions_db


def test_get_override_defaults_false_when_never_set(tmp_path: Path):
    db = tmp_path / "sessions.db"
    assert context_overrides.get_override("s1", path=db) is False


def test_set_then_get_on(tmp_path: Path):
    db = tmp_path / "sessions.db"
    context_overrides.set_override("s1", "on", path=db)
    assert context_overrides.get_override("s1", path=db) is True


def test_set_then_get_off(tmp_path: Path):
    db = tmp_path / "sessions.db"
    context_overrides.set_override("s1", "on", path=db)
    context_overrides.set_override("s1", "off", path=db)
    assert context_overrides.get_override("s1", path=db) is False


def test_override_is_per_session(tmp_path: Path):
    db = tmp_path / "sessions.db"
    context_overrides.set_override("s1", "on", path=db)
    assert context_overrides.get_override("s2", path=db) is False


def test_get_override_on_missing_db_returns_false(tmp_path: Path):
    assert context_overrides.get_override("s1", path=tmp_path / "nope.db") is False


def test_get_override_on_corrupt_db_returns_false(tmp_path: Path):
    db = tmp_path / "sessions.db"
    db.write_bytes(b"not a sqlite file")
    assert context_overrides.get_override("s1", path=db) is False


def test_set_override_overwrites(tmp_path: Path):
    db = tmp_path / "sessions.db"
    context_overrides.set_override("s1", "on", path=db)
    context_overrides.set_override("s1", "on", path=db)  # idempotent, no UNIQUE-constraint error
    assert context_overrides.get_override("s1", path=db) is True
