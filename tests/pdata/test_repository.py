from __future__ import annotations

from cc_session_tools.lib.pdata import repository


def test_connect_creates_records_and_record_group_fields_tables(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "records" in tables
        assert "record_group_fields" in tables

        record_cols = {r["name"] for r in conn.execute('PRAGMA table_info("records")')}
        assert record_cols == {
            "id", "record_group", "content", "file_path",
            "created_at", "updated_at", "version", "deleted_at",
        }

        field_cols = {r["name"] for r in conn.execute('PRAGMA table_info("record_group_fields")')}
        assert field_cols == {"record_group", "field_name", "description", "added_at"}
    finally:
        conn.close()


def test_connect_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    repository.connect("testproj").close()
    conn = repository.connect("testproj")  # must not raise on re-run
    conn.close()


def test_connect_rejects_unsafe_project_name(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    import pytest
    with pytest.raises(ValueError, match="project"):
        repository.connect("../escape")


def test_insert_base_record_then_get_by_id(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        record_id = repository.insert_base_record(
            conn, record_group="ccst-ideas", content="an idea", file_path=None,
            created_at=1000, updated_at=1000,
        )
        assert record_id == 1
        row = repository.get_base_record(conn, record_id)
        assert row is not None
        assert row["record_group"] == "ccst-ideas"
        assert row["content"] == "an idea"
        assert row["file_path"] is None
        assert row["created_at"] == 1000
        assert row["updated_at"] == 1000
        assert row["version"] == 1
        assert row["deleted_at"] is None
    finally:
        conn.close()


def test_get_base_record_returns_none_for_missing_id(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        assert repository.get_base_record(conn, 999) is None
    finally:
        conn.close()
