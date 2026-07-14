"""Tests for cc_session_tools.lib.sessions_db — the sessions.db store."""
from __future__ import annotations

import sqlite3
import time
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


# ---------- sessions table ----------

def test_ensure_session_row_inserts_new_row(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-my-feature", path=db_path)
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].basename == "20260713-my-feature"
    assert rows[0].project_dir == proj
    assert rows[0].start_date == "20260713"
    assert rows[0].last_opened == 0.0
    assert rows[0].last_active == 0.0


def test_ensure_session_row_is_idempotent_and_does_not_clobber_timestamps(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-my-feature", path=db_path, when=1000.0)
    sessions_db.ensure_session_row(proj, "20260713-my-feature", path=db_path)
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].last_opened == 1000.0


def test_ensure_session_row_rejects_non_session_basename(db_path):
    sessions_db.ensure_session_row(Path("/repos/myproj"), "not-a-session-name", path=db_path)
    assert sessions_db.list_sessions(path=db_path) == []


def test_touch_last_opened_sets_column(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-foo", path=db_path, when=1234.5)
    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].last_opened == 1234.5
    assert rows[0].last_active == 0.0


def test_touch_last_active_sets_column(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_active(proj, "20260713-foo", path=db_path, when=5678.5)
    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].last_active == 5678.5
    assert rows[0].last_opened == 0.0


def test_touch_last_opened_defaults_to_now_when_no_when_given(db_path):
    before = time.time()
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-foo", path=db_path)
    after = time.time()
    rows = sessions_db.list_sessions(path=db_path)
    assert before <= rows[0].last_opened <= after


def test_touch_last_opened_updates_existing_row_in_place(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-foo", path=db_path, when=100.0)
    sessions_db.touch_last_opened(proj, "20260713-foo", path=db_path, when=200.0)
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].last_opened == 200.0


def test_list_sessions_scoped_to_project_dir(db_path):
    a = Path("/repos/proj-a")
    b = Path("/repos/proj-b")
    sessions_db.ensure_session_row(a, "20260713-in-a", path=db_path)
    sessions_db.ensure_session_row(b, "20260713-in-b", path=db_path)
    rows = sessions_db.list_sessions(project_dir=a, path=db_path)
    assert [r.basename for r in rows] == ["20260713-in-a"]


def test_list_sessions_empty_db_returns_empty_list(db_path):
    assert not db_path.exists()
    assert sessions_db.list_sessions(path=db_path) == []


def test_list_sessions_limit_returns_most_recent_n_by_last_active(db_path):
    """'Most recent N' must be an indexed ORDER BY ... LIMIT, not fetch-all-then-slice —
    this is the 2026-07-13 design-spec requirement. Seed enough rows that a naive
    Python-side sort+slice would still pass, then assert exactly `limit` rows come back
    in the right order, proving the LIMIT clause itself is doing the work."""
    proj = Path("/repos/myproj")
    for i in range(20):
        sessions_db.ensure_session_row(proj, f"20260713-sess-{i:02d}", path=db_path)
        sessions_db.touch_last_active(proj, f"20260713-sess-{i:02d}", path=db_path, when=float(i))
    rows = sessions_db.list_sessions(order_by="last_active", limit=5, path=db_path)
    assert len(rows) == 5
    assert [r.basename for r in rows] == [
        "20260713-sess-19", "20260713-sess-18", "20260713-sess-17",
        "20260713-sess-16", "20260713-sess-15",
    ]


def test_list_sessions_limit_larger_than_row_count_returns_all(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-only", path=db_path)
    rows = sessions_db.list_sessions(order_by="last_active", limit=100, path=db_path)
    assert len(rows) == 1


def test_list_sessions_rejects_limit_without_order_by(db_path):
    with pytest.raises(ValueError, match="order_by"):
        sessions_db.list_sessions(limit=5, path=db_path)


def test_list_sessions_rejects_unknown_order_by_column(db_path):
    with pytest.raises(ValueError, match="order_by"):
        sessions_db.list_sessions(order_by="start_date", path=db_path)  # not DB-orderable — see docstring


def test_find_exact_matches_basename(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-exact-match", path=db_path)
    rows = sessions_db.find_exact("20260713-exact-match", path=db_path)
    assert len(rows) == 1
    assert rows[0].project_dir == proj


def test_find_exact_no_match_returns_empty_list(db_path):
    sessions_db.ensure_session_row(Path("/repos/p"), "20260713-a", path=db_path)
    assert sessions_db.find_exact("20260713-b", path=db_path) == []


def test_find_exact_on_nonexistent_db_returns_empty_list(db_path):
    assert not db_path.exists()
    assert sessions_db.find_exact("20260713-a", path=db_path) == []
