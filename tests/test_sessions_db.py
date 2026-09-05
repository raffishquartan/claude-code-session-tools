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


def _tag_created_at(db_path, uuid):
    conn = sessions_db.connect(path=db_path)
    try:
        row = conn.execute(
            "SELECT created_at FROM session_tags WHERE uuid=?", (uuid,)
        ).fetchone()
    finally:
        conn.close()
    return row["created_at"]


def test_write_tag_sets_created_at_and_preserves_it_on_upsert(db_path):
    sessions_db.write_tag("uuid-1", "old-tag", path=db_path)
    first_created_at = _tag_created_at(db_path, "uuid-1")
    assert first_created_at is not None

    sessions_db.write_tag("uuid-1", "new-tag", path=db_path)
    assert _tag_created_at(db_path, "uuid-1") == first_created_at


def test_schema_has_expected_tables(db_path):
    sessions_db.write_tag("uuid-1", "my-feature", path=db_path)  # bootstrap schema
    conn = sqlite3.connect(str(db_path))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {
        "session_tags", "sessions", "sessions_migration_ambiguous", "doctor_mutes", "install_sync",
    } <= names


# ---------- schema: uuid-aware primary key ----------

def test_fresh_db_sessions_pk_is_project_basename_uuid(db_path):
    conn = sessions_db.connect(path=db_path)
    try:
        assert sessions_db.sessions_schema_is_uuid_keyed(conn)
    finally:
        conn.close()


def test_two_forks_of_same_basename_produce_two_rows_not_an_overwrite(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-my-feature", uuid="uuid-a", path=db_path, when=1.0)
    sessions_db.touch_last_opened(proj, "20260713-my-feature", uuid="uuid-b", path=db_path, when=2.0)
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 2
    assert {r.uuid for r in rows} == {"uuid-a", "uuid-b"}
    assert {r.last_opened for r in rows} == {1.0, 2.0}


def test_fresh_db_records_uuid_migration_marker_immediately(db_path):
    assert not db_path.exists()
    conn = sessions_db.connect(path=db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM migrations WHERE name = ?", (sessions_db.SESSIONS_UUID_MIGRATION,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None


def test_sessions_schema_is_uuid_keyed_false_for_old_two_column_pk(db_path):
    # Hand-build the pre-3.0.0 schema — sessions_db.connect() must never be called first,
    # since that would create the current (3-column) schema instead.
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE sessions (project_dir TEXT NOT NULL, basename TEXT NOT NULL, "
        "start_date TEXT NOT NULL, PRIMARY KEY (project_dir, basename))"
    )
    conn.row_factory = sqlite3.Row
    assert sessions_db.sessions_schema_is_uuid_keyed(conn) is False
    conn.close()


def test_sessions_schema_is_uuid_keyed_false_when_uuid_column_added_but_not_in_pk(db_path):
    # db.add_missing_columns()-style bare ALTER TABLE ADD COLUMN: a uuid column exists but
    # is not part of the primary key — must NOT be mistaken for a real migration (this is
    # the exact trap design.md Decision 1-2 calls out).
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE sessions (project_dir TEXT NOT NULL, basename TEXT NOT NULL, "
        "uuid TEXT NOT NULL DEFAULT '', start_date TEXT NOT NULL, "
        "PRIMARY KEY (project_dir, basename))"
    )
    conn.row_factory = sqlite3.Row
    assert sessions_db.sessions_schema_is_uuid_keyed(conn) is False
    conn.close()


# ---------- sessions table ----------

def test_ensure_session_row_inserts_new_row(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-my-feature", uuid="uuid-1", path=db_path)
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].basename == "20260713-my-feature"
    assert rows[0].uuid == "uuid-1"
    assert rows[0].project_dir == proj
    assert rows[0].start_date == "20260713"
    assert rows[0].last_opened == 0.0
    assert rows[0].last_active == 0.0


def test_ensure_session_row_is_idempotent_and_does_not_clobber_timestamps(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-my-feature", uuid="uuid-1", path=db_path, when=1000.0)
    sessions_db.ensure_session_row(proj, "20260713-my-feature", uuid="uuid-1", path=db_path)
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].last_opened == 1000.0


def test_ensure_session_row_rejects_non_session_basename(db_path):
    sessions_db.ensure_session_row(Path("/repos/myproj"), "not-a-session-name", uuid="uuid-1", path=db_path)
    assert sessions_db.list_sessions(path=db_path) == []


def test_ensure_session_row_rejects_relative_project_dir_and_logs(db_path, capsys):
    sessions_db.ensure_session_row(Path("."), "20260713-my-feature", uuid="uuid-1", path=db_path)
    assert sessions_db.list_sessions(path=db_path) == []
    err = capsys.readouterr().err
    assert "[sessions-db]" in err
    assert "20260713-my-feature" in err
    assert "not absolute" in err
    assert "ccst repair sessions" in err


def test_touch_last_opened_rejects_relative_project_dir_and_logs(db_path, capsys):
    sessions_db.touch_last_opened(Path("cc-sessions/x"), "20260713-foo", uuid="uuid-1", path=db_path)
    assert sessions_db.list_sessions(path=db_path) == []
    err = capsys.readouterr().err
    assert "[sessions-db]" in err
    assert "20260713-foo" in err
    assert "not absolute" in err
    assert "ccst repair sessions" in err


def test_touch_last_active_rejects_relative_project_dir_and_logs(db_path, capsys):
    sessions_db.touch_last_active(Path("cc-sessions/x"), "20260713-foo", uuid="uuid-1", path=db_path)
    assert sessions_db.list_sessions(path=db_path) == []
    err = capsys.readouterr().err
    assert "[sessions-db]" in err
    assert "20260713-foo" in err
    assert "not absolute" in err
    assert "ccst repair sessions" in err


def test_touch_last_opened_sets_column(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-foo", uuid="uuid-1", path=db_path, when=1234.5)
    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].last_opened == 1234.5
    assert rows[0].last_active == 0.0


def test_touch_last_active_sets_column(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_active(proj, "20260713-foo", uuid="uuid-1", path=db_path, when=5678.5)
    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].last_active == 5678.5
    assert rows[0].last_opened == 0.0


def _row_updated_at(db_path, project_dir, basename, uuid):
    conn = sessions_db.connect(path=db_path)
    try:
        row = conn.execute(
            "SELECT updated_at FROM sessions WHERE project_dir=? AND basename=? AND uuid=?",
            (str(project_dir), basename, uuid),
        ).fetchone()
    finally:
        conn.close()
    return row["updated_at"]


def test_ensure_session_row_leaves_updated_at_null(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-foo", uuid="uuid-1", path=db_path)
    assert _row_updated_at(db_path, proj, "20260713-foo", "uuid-1") is None


def test_touch_last_opened_bumps_updated_at(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-foo", uuid="uuid-1", path=db_path, when=1234.5)
    assert _row_updated_at(db_path, proj, "20260713-foo", "uuid-1") is not None


def test_touch_last_active_bumps_updated_at(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_active(proj, "20260713-foo", uuid="uuid-1", path=db_path, when=5678.5)
    assert _row_updated_at(db_path, proj, "20260713-foo", "uuid-1") is not None


def test_touch_last_opened_defaults_to_now_when_no_when_given(db_path):
    before = time.time()
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-foo", uuid="uuid-1", path=db_path)
    after = time.time()
    rows = sessions_db.list_sessions(path=db_path)
    assert before <= rows[0].last_opened <= after


def test_touch_last_opened_updates_existing_row_in_place(db_path):
    proj = Path("/repos/myproj")
    sessions_db.touch_last_opened(proj, "20260713-foo", uuid="uuid-1", path=db_path, when=100.0)
    sessions_db.touch_last_opened(proj, "20260713-foo", uuid="uuid-1", path=db_path, when=200.0)
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].last_opened == 200.0


def test_touch_last_opened_reconciles_matching_ambiguous_sidecar_row(db_path):
    """A sidecar row left by the migration for this basename must be deleted the moment
    the session is genuinely opened again with its real uuid - not left dangling, and not
    causing the real write to be rejected (see design.md Decision 7)."""
    proj = Path("/repos/myproj")
    sessions_db.record_ambiguous_row(
        proj, "20260713-foo", "no-transcript-found",
        start_date="20260713", discovered_at="2026-07-13T00:00:00Z", path=db_path,
    )
    sessions_db.touch_last_opened(proj, "20260713-foo", uuid="uuid-real", path=db_path, when=100.0)

    assert sessions_db.list_ambiguous_rows(path=db_path) == []
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].uuid == "uuid-real"


def test_list_sessions_scoped_to_project_dir(db_path):
    a = Path("/repos/proj-a")
    b = Path("/repos/proj-b")
    sessions_db.ensure_session_row(a, "20260713-in-a", uuid="uuid-a", path=db_path)
    sessions_db.ensure_session_row(b, "20260713-in-b", uuid="uuid-b", path=db_path)
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
        sessions_db.ensure_session_row(proj, f"20260713-sess-{i:02d}", uuid=f"uuid-{i:02d}", path=db_path)
        sessions_db.touch_last_active(
            proj, f"20260713-sess-{i:02d}", uuid=f"uuid-{i:02d}", path=db_path, when=float(i)
        )
    rows = sessions_db.list_sessions(order_by="last_active", limit=5, path=db_path)
    assert len(rows) == 5
    assert [r.basename for r in rows] == [
        "20260713-sess-19", "20260713-sess-18", "20260713-sess-17",
        "20260713-sess-16", "20260713-sess-15",
    ]


def test_list_sessions_limit_larger_than_row_count_returns_all(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-only", uuid="uuid-1", path=db_path)
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
    sessions_db.ensure_session_row(proj, "20260713-exact-match", uuid="uuid-1", path=db_path)
    rows = sessions_db.find_exact("20260713-exact-match", path=db_path)
    assert len(rows) == 1
    assert rows[0].project_dir == proj


def test_find_exact_returns_every_fork(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-forked", uuid="uuid-a", path=db_path)
    sessions_db.ensure_session_row(proj, "20260713-forked", uuid="uuid-b", path=db_path)
    rows = sessions_db.find_exact("20260713-forked", path=db_path)
    assert {r.uuid for r in rows} == {"uuid-a", "uuid-b"}


def test_find_exact_no_match_returns_empty_list(db_path):
    sessions_db.ensure_session_row(Path("/repos/p"), "20260713-a", uuid="uuid-1", path=db_path)
    assert sessions_db.find_exact("20260713-b", path=db_path) == []


def test_find_exact_on_nonexistent_db_returns_empty_list(db_path):
    assert not db_path.exists()
    assert sessions_db.find_exact("20260713-a", path=db_path) == []


# ---------- delete_session_row / delete_tag ----------

def test_delete_session_row_removes_matching_row(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-gone", uuid="uuid-1", path=db_path)
    sessions_db.ensure_session_row(proj, "20260713-stays", uuid="uuid-2", path=db_path)
    assert sessions_db.delete_session_row(proj, "20260713-gone", path=db_path) is True
    assert [r.basename for r in sessions_db.list_sessions(path=db_path)] == ["20260713-stays"]


def test_delete_session_row_removes_every_fork(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-forked", uuid="uuid-a", path=db_path)
    sessions_db.ensure_session_row(proj, "20260713-forked", uuid="uuid-b", path=db_path)
    assert sessions_db.delete_session_row(proj, "20260713-forked", path=db_path) is True
    assert sessions_db.list_sessions(path=db_path) == []


def test_delete_session_row_exact_removes_only_the_named_fork(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-forked", uuid="uuid-a", path=db_path)
    sessions_db.ensure_session_row(proj, "20260713-forked", uuid="uuid-b", path=db_path)
    assert sessions_db.delete_session_row_exact(proj, "20260713-forked", "uuid-a", path=db_path) is True
    rows = sessions_db.list_sessions(path=db_path)
    assert [r.uuid for r in rows] == ["uuid-b"]


def test_delete_session_row_exact_returns_false_when_absent(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-only", uuid="uuid-1", path=db_path)
    assert sessions_db.delete_session_row_exact(proj, "20260713-only", "uuid-nope", path=db_path) is False


def test_delete_session_row_returns_false_when_absent(db_path):
    proj = Path("/repos/myproj")
    sessions_db.ensure_session_row(proj, "20260713-only", uuid="uuid-1", path=db_path)
    assert sessions_db.delete_session_row(proj, "20260713-nope", path=db_path) is False


def test_delete_tag_removes_tag(db_path):
    sessions_db.write_tag("uuid-1", "feature", path=db_path)
    assert sessions_db.delete_tag("uuid-1", path=db_path) is True
    assert sessions_db.lookup_tags(["uuid-1"], path=db_path) == {}


def test_delete_tag_returns_false_when_absent(db_path):
    sessions_db.write_tag("uuid-1", "feature", path=db_path)
    assert sessions_db.delete_tag("uuid-missing", path=db_path) is False


def test_connect_creates_migrations_table(db_path):
    conn = sessions_db.connect(path=db_path)
    try:
        names = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert "migrations" in names


# ---------- sessions_migration_ambiguous ----------

def test_record_and_list_ambiguous_row(db_path):
    proj = Path("/repos/myproj")
    sessions_db.record_ambiguous_row(
        proj, "20260713-mystery", "no-transcript-found",
        start_date="20260713", discovered_at="2026-07-13T00:00:00Z", path=db_path,
    )
    rows = sessions_db.list_ambiguous_rows(path=db_path)
    assert len(rows) == 1
    assert rows[0].project_dir == proj
    assert rows[0].basename == "20260713-mystery"
    assert rows[0].reason == "no-transcript-found"


def test_list_ambiguous_rows_on_nonexistent_db_returns_empty_list(db_path):
    assert not db_path.exists()
    assert sessions_db.list_ambiguous_rows(path=db_path) == []


def test_resolve_ambiguous_row_moves_it_into_sessions(db_path):
    proj = Path("/repos/myproj")
    sessions_db.record_ambiguous_row(
        proj, "20260713-mystery", "no-transcript-found",
        start_date="20260713", discovered_at="2026-07-13T00:00:00Z", path=db_path,
    )
    assert sessions_db.resolve_ambiguous_row(proj, "20260713-mystery", "real-uuid", path=db_path) is True

    assert sessions_db.list_ambiguous_rows(path=db_path) == []
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].uuid == "real-uuid"
    assert rows[0].start_date == "20260713"


def test_resolve_ambiguous_row_returns_false_when_absent(db_path):
    assert sessions_db.resolve_ambiguous_row(
        Path("/repos/p"), "20260713-none", "uuid-x", path=db_path
    ) is False


def test_delete_ambiguous_row_removes_it(db_path):
    proj = Path("/repos/myproj")
    sessions_db.record_ambiguous_row(
        proj, "20260713-mystery", "no-transcript-found",
        start_date="20260713", discovered_at="2026-07-13T00:00:00Z", path=db_path,
    )
    assert sessions_db.delete_ambiguous_row(proj, "20260713-mystery", path=db_path) is True
    assert sessions_db.list_ambiguous_rows(path=db_path) == []


def test_delete_ambiguous_row_returns_false_when_absent(db_path):
    assert sessions_db.delete_ambiguous_row(Path("/repos/p"), "20260713-none", path=db_path) is False
