"""Tests for cc_session_tools.lib.doctor_mutes — now SQLite-backed (sessions.db,
doctor_mutes table). Zero test coverage existed for this module before this phase."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import doctor_mutes


@pytest.fixture
def mutes_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.db"


def test_load_mutes_empty_when_file_absent(mutes_path):
    assert not mutes_path.exists()
    assert doctor_mutes.load_mutes(mutes_path) == {}


def test_load_mutes_empty_for_a_corrupt_db(mutes_path):
    """Found while auditing sessions.db read sites for the same lazy-open
    corruption gap fixed elsewhere on this branch (install_sync.py,
    doctor.py's check_sessions_project_dir_absolute): sqlite3.connect()
    opens lazily and only fails once a statement touches the file, so a
    corrupt sessions.db reaches the SELECT here as sqlite3.DatabaseError,
    not connect() as OperationalError. `ccst doctor --list-mutes`/`--drift`
    must never crash on this - doctor is exempt from the install-sync gate
    specifically so it always works."""
    mutes_path.write_bytes(b"not a sqlite database file")
    assert doctor_mutes.load_mutes(mutes_path) == {}


def test_add_mute_then_load_returns_it(mutes_path):
    doctor_mutes.add_mute(mutes_path, "version:pypi", today="2026-07-13")
    assert doctor_mutes.load_mutes(mutes_path) == {"version:pypi": "2026-07-13"}


def test_add_mute_returns_full_mute_map(mutes_path):
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    result = doctor_mutes.add_mute(mutes_path, "b", today="2026-07-02")
    assert result == {"a": "2026-07-01", "b": "2026-07-02"}


def test_add_mute_overwrites_existing_date(mutes_path):
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-13")
    assert doctor_mutes.load_mutes(mutes_path) == {"a": "2026-07-13"}


def test_remove_mute_returns_true_when_present(mutes_path):
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    assert doctor_mutes.remove_mute(mutes_path, "a") is True
    assert doctor_mutes.load_mutes(mutes_path) == {}


def test_remove_mute_returns_false_when_absent(mutes_path):
    assert not mutes_path.exists()
    assert doctor_mutes.remove_mute(mutes_path, "nope") is False


def test_remove_mute_leaves_other_entries_intact(mutes_path):
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    doctor_mutes.add_mute(mutes_path, "b", today="2026-07-02")
    doctor_mutes.remove_mute(mutes_path, "a")
    assert doctor_mutes.load_mutes(mutes_path) == {"b": "2026-07-02"}


def test_default_mutes_path_matches_sessions_db_default(tmp_path, monkeypatch):
    from cc_session_tools.lib import sessions_db
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    assert doctor_mutes.default_mutes_path() == sessions_db.default_db_path()


def test_mutes_share_file_with_session_tags(mutes_path):
    """doctor_mutes and session_tags live in the same sessions.db file."""
    from cc_session_tools.lib import sessions_db
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    sessions_db.write_tag("uuid-1", "my-tag", path=mutes_path)
    assert doctor_mutes.load_mutes(mutes_path) == {"a": "2026-07-01"}
    assert sessions_db.lookup_tags(["uuid-1"], path=mutes_path) == {"uuid-1": "my-tag"}


def _created_at(mutes_path: Path, name: str) -> object:
    from cc_session_tools.lib import sessions_db
    conn = sessions_db.connect(path=mutes_path)
    try:
        row = conn.execute(
            "SELECT created_at FROM doctor_mutes WHERE name=?", (name,)
        ).fetchone()
    finally:
        conn.close()
    return row["created_at"]


def test_add_mute_sets_created_at_and_preserves_it_on_re_mute(mutes_path):
    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-01")
    first_created_at = _created_at(mutes_path, "a")
    assert first_created_at is not None

    doctor_mutes.add_mute(mutes_path, "a", today="2026-07-05")  # re-mute
    assert _created_at(mutes_path, "a") == first_created_at  # first-muted, not last-muted
    assert doctor_mutes.load_mutes(mutes_path)["a"] == "2026-07-05"  # muted_at still updates
