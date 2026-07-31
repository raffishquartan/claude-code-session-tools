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
