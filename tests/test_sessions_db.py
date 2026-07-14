"""Tests for cc_session_tools.lib.sessions_db — the sessions.db store."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cc_session_tools.lib import sessions_db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.db"


# ---------- default_db_path / env override ----------

def test_default_db_path_honours_ccst_sessions_dir_env(tmp_path, monkeypatch):
    override = tmp_path / "custom-dir"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(override))
    assert sessions_db.default_db_path() == override / "sessions.db"


def test_default_db_path_falls_back_to_data_home(tmp_path, monkeypatch):
    monkeypatch.delenv("CCST_SESSIONS_DIR", raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    assert sessions_db.default_db_path() == tmp_path / "data-home" / "sessions.db"


# ---------- session_tags ----------

def test_write_tag_then_lookup_returns_tag(db_path):
    sessions_db.write_tag("uuid-1", "my-feature", path=db_path)
    result = sessions_db.lookup_tags(["uuid-1"], path=db_path)
    assert result == {"uuid-1": "my-feature"}


def test_lookup_tags_returns_empty_dict_for_unknown_uuids(db_path):
    sessions_db.write_tag("uuid-1", "my-feature", path=db_path)
    result = sessions_db.lookup_tags(["uuid-2", "uuid-3"], path=db_path)
    assert result == {}


def test_lookup_tags_batches_multiple_uuids(db_path):
    sessions_db.write_tag("uuid-1", "tag-one", path=db_path)
    sessions_db.write_tag("uuid-2", "tag-two", path=db_path)
    result = sessions_db.lookup_tags(["uuid-1", "uuid-2", "uuid-missing"], path=db_path)
    assert result == {"uuid-1": "tag-one", "uuid-2": "tag-two"}


def test_lookup_tags_empty_list_returns_empty_dict_without_opening_db(db_path):
    # db_path does not exist yet — must not raise.
    assert sessions_db.lookup_tags([], path=db_path) == {}
    assert not db_path.exists()


def test_lookup_tags_on_nonexistent_db_returns_empty_dict(db_path):
    # No writer has ever run — readonly connect() would raise OperationalError;
    # lookup_tags must degrade gracefully instead of propagating it.
    assert not db_path.exists()
    assert sessions_db.lookup_tags(["uuid-1"], path=db_path) == {}


def test_write_tag_upserts_on_conflict(db_path):
    sessions_db.write_tag("uuid-1", "old-tag", path=db_path)
    sessions_db.write_tag("uuid-1", "new-tag", path=db_path)
    assert sessions_db.lookup_tags(["uuid-1"], path=db_path) == {"uuid-1": "new-tag"}


def test_write_tag_creates_db_file(db_path):
    assert not db_path.exists()
    sessions_db.write_tag("uuid-1", "my-feature", path=db_path)
    assert db_path.exists()


def test_schema_has_three_tables(db_path):
    sessions_db.write_tag("uuid-1", "my-feature", path=db_path)  # bootstrap schema
    conn = sqlite3.connect(str(db_path))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"session_tags", "sessions", "doctor_mutes"} <= names
