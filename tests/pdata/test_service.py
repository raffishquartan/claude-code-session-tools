from __future__ import annotations

import pytest

from cc_session_tools.lib.pdata import service


def test_add_record_content_only(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(
        project="testproj", record_group="ccst-ideas", content="an idea",
        file_path=None, fields={}, created_at=1000,
    )
    assert record.id == 1
    assert record.record_group == "ccst-ideas"
    assert record.content == "an idea"
    assert record.fields == {}


def test_add_record_rejects_invalid_record_group(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="record_group"):
        service.add_record(
            project="testproj", record_group="Not Valid", content="x",
            file_path=None, fields={}, created_at=1000,
        )


def test_add_record_rejects_absolute_file_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="relative"):
        service.add_record(
            project="testproj", record_group="filings", content="x",
            file_path="/etc/passwd", fields={}, created_at=1000,
        )


def test_add_record_rejects_path_traversal_file_path(monkeypatch, tmp_path):
    """Regression test: a relative-but-escaping --file (no leading '/') must be rejected too,
    since Plan B later resolves file_path against the project root."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="\\.\\."):
        service.add_record(
            project="testproj", record_group="filings", content="x",
            file_path="../../etc/passwd", fields={}, created_at=1000,
        )


def test_schema_add_field_creates_column_and_description(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description="who sent it", default=None,
    )
    from cc_session_tools.lib.pdata import repository
    conn = repository.connect("testproj")
    try:
        cols = repository.list_extension_columns(conn, "key-events")
        assert "sender" in cols
        row = conn.execute(
            "SELECT description FROM record_group_fields WHERE record_group=? AND field_name=?",
            ("key-events", "sender"),
        ).fetchone()
        assert row["description"] == "who sent it"
    finally:
        conn.close()


def test_schema_add_field_without_description_leaves_it_blank(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description=None, default=None,
    )
    from cc_session_tools.lib.pdata import repository
    conn = repository.connect("testproj")
    try:
        row = conn.execute(
            "SELECT description FROM record_group_fields WHERE record_group=? AND field_name=?",
            ("key-events", "sender"),
        ).fetchone()
        assert row is None  # no --description given -> no row written at all
    finally:
        conn.close()


def test_schema_add_field_rejects_invalid_record_group(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="record_group"):
        service.schema_add_field(
            project="testproj", record_group="Bad Group", field_name="x",
            sql_type="TEXT", description=None, default=None,
        )


def test_schema_add_field_rerun_updates_description_without_duplicating_column(
    monkeypatch, tmp_path,
):
    """Regression test for the re-run-to-edit-description path (spec §10's open question about
    a possible edit-description command): calling schema_add_field again for a column that
    already exists must fall through add_extension_column's early-return and still reach
    upsert_field_description, overwriting the description rather than erroring or duplicating
    the (record_group, field_name) row."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description="who sent it", default=None,
    )
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description="updated description", default=None,
    )
    from cc_session_tools.lib.pdata import repository
    conn = repository.connect("testproj")
    try:
        cols = repository.list_extension_columns(conn, "key-events")
        assert cols.count("sender") == 1
        rows = conn.execute(
            "SELECT description FROM record_group_fields WHERE record_group=? AND field_name=?",
            ("key-events", "sender"),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["description"] == "updated description"
    finally:
        conn.close()
