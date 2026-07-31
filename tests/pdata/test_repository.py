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


def test_ensure_extension_table_creates_table_with_record_id_pk(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.ensure_extension_table(conn, "key-events")
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "ext_key_events" in tables
        cols = {r["name"] for r in conn.execute('PRAGMA table_info("ext_key_events")')}
        assert cols == {"record_id"}
    finally:
        conn.close()


def test_ensure_extension_table_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.ensure_extension_table(conn, "key-events")
        with repository._immediate(conn):
            repository.ensure_extension_table(conn, "key-events")  # must not raise
    finally:
        conn.close()


def test_add_extension_column_creates_typed_column(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
        cols = {r["name"]: r["type"] for r in conn.execute('PRAGMA table_info("ext_key_events")')}
        assert cols["sender"] == "TEXT"
    finally:
        conn.close()


def test_add_extension_column_with_default(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "is_read", "INTEGER", default=0)
            repository.insert_base_record(
                conn, record_group="key-events", content="x", file_path=None,
                created_at=1, updated_at=1,
            )
            conn.execute(
                'INSERT INTO "ext_key_events" (record_id) VALUES (1)'
            )
        row = conn.execute('SELECT is_read FROM "ext_key_events" WHERE record_id=1').fetchone()
        assert row["is_read"] == 0
    finally:
        conn.close()


def test_add_extension_column_is_idempotent_noop_if_column_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
        cols = [r["name"] for r in conn.execute('PRAGMA table_info("ext_key_events")')]
        assert cols.count("sender") == 1
    finally:
        conn.close()


def test_add_extension_column_rejects_bad_type(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    import pytest
    conn = repository.connect("testproj")
    try:
        with pytest.raises(ValueError, match="type"):
            with repository._immediate(conn):
                repository.add_extension_column(conn, "key-events", "x", "DROP TABLE records", default=None)
    finally:
        conn.close()


def test_add_extension_column_with_text_default_escapes_quotes(monkeypatch, tmp_path):
    """Regression test for the DDL-DEFAULT bound-parameter bug: ALTER TABLE ADD COLUMN ...
    DEFAULT ? is not valid SQLite (confirmed: raises OperationalError), so the default must be
    embedded as an escaped literal instead."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(
                conn, "key-events", "note", "TEXT", default="it's fine",
            )
            record_id = repository.insert_base_record(
                conn, record_group="key-events", content="x", file_path=None,
                created_at=1, updated_at=1,
            )
            repository.insert_extension_row(conn, "key-events", record_id, {})
        row = conn.execute(
            'SELECT note FROM "ext_key_events" WHERE record_id=?', (record_id,)
        ).fetchone()
        assert row["note"] == "it's fine"
    finally:
        conn.close()


def test_add_extension_column_with_invalid_integer_default_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    import pytest
    conn = repository.connect("testproj")
    try:
        with pytest.raises(ValueError, match="INTEGER"):
            with repository._immediate(conn):
                repository.add_extension_column(
                    conn, "key-events", "count", "INTEGER", default="not-a-number",
                )
    finally:
        conn.close()


def test_ensure_extension_table_backfills_rows_that_predate_it(monkeypatch, tmp_path):
    """Regression test for the missing-backfill bug: a record_group that already had rows
    before its first schema add-field call must still get an ext row for each of those rows —
    otherwise a later `update --field` on one of them silently updates zero rows."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            pre_existing_id = repository.insert_base_record(
                conn, record_group="notes", content="already here", file_path=None,
                created_at=1, updated_at=1,
            )
        with repository._immediate(conn):
            repository.add_extension_column(conn, "notes", "priority", "INTEGER", default=None)
        ext_row = repository.get_extension_row(conn, "notes", pre_existing_id)
        assert ext_row is not None
        assert ext_row["priority"] is None

        # And update_extension_row (Task 15) must be able to find that backfilled row —
        # verified here directly against the raw UPDATE, since update_extension_row itself
        # isn't defined until Task 15.
        cur = conn.execute(
            'UPDATE "ext_notes" SET priority=? WHERE record_id=?', (5, pre_existing_id),
        )
        assert cur.rowcount == 1
    finally:
        conn.close()


def test_upsert_field_description(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.upsert_field_description(
                conn, record_group="key-events", field_name="sender",
                description="who sent it", added_at=1000,
            )
        row = conn.execute(
            "SELECT * FROM record_group_fields WHERE record_group=? AND field_name=?",
            ("key-events", "sender"),
        ).fetchone()
        assert row["description"] == "who sent it"
        assert row["added_at"] == 1000

        # Re-run with a new description — must overwrite, not duplicate (idempotent upsert).
        with repository._immediate(conn):
            repository.upsert_field_description(
                conn, record_group="key-events", field_name="sender",
                description="updated", added_at=1000,
            )
        rows = conn.execute(
            "SELECT * FROM record_group_fields WHERE record_group=? AND field_name=?",
            ("key-events", "sender"),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["description"] == "updated"
    finally:
        conn.close()


def test_list_record_groups_returns_counts_and_ext_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.insert_base_record(
                conn, record_group="ccst-ideas", content="a", file_path=None,
                created_at=1, updated_at=1,
            )
            repository.insert_base_record(
                conn, record_group="ccst-ideas", content="b", file_path=None,
                created_at=2, updated_at=5,
            )
            repository.insert_base_record(
                conn, record_group="filings", content="c", file_path=None,
                created_at=3, updated_at=3,
            )
            repository.add_extension_column(conn, "filings", "doc_type", "TEXT", default=None)
        groups = {g["record_group"]: g for g in repository.list_record_groups(conn)}
        assert groups["ccst-ideas"]["row_count"] == 2
        assert groups["ccst-ideas"]["has_extension_table"] is False
        assert groups["ccst-ideas"]["max_updated_at"] == 5
        assert groups["filings"]["row_count"] == 1
        assert groups["filings"]["has_extension_table"] is True
    finally:
        conn.close()


def test_list_record_groups_excludes_soft_deleted_from_row_count(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            rid = repository.insert_base_record(
                conn, record_group="notes", content="a", file_path=None,
                created_at=1, updated_at=1,
            )
            conn.execute("UPDATE records SET deleted_at=? WHERE id=?", (2, rid))
        groups = {g["record_group"]: g for g in repository.list_record_groups(conn)}
        assert groups["notes"]["row_count"] == 0
    finally:
        conn.close()


def test_insert_extension_row(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
            record_id = repository.insert_base_record(
                conn, record_group="key-events", content="x", file_path=None,
                created_at=1, updated_at=1,
            )
            repository.insert_extension_row(
                conn, "key-events", record_id, {"sender": "alice"},
            )
        row = conn.execute(
            'SELECT * FROM "ext_key_events" WHERE record_id=?', (record_id,)
        ).fetchone()
        assert row["sender"] == "alice"
    finally:
        conn.close()


def test_insert_extension_row_with_no_fields_still_creates_row(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("testproj")
    try:
        with repository._immediate(conn):
            repository.add_extension_column(conn, "key-events", "sender", "TEXT", default=None)
            record_id = repository.insert_base_record(
                conn, record_group="key-events", content="x", file_path=None,
                created_at=1, updated_at=1,
            )
            repository.insert_extension_row(conn, "key-events", record_id, {})
        row = conn.execute(
            'SELECT * FROM "ext_key_events" WHERE record_id=?', (record_id,)
        ).fetchone()
        assert row is not None
        assert row["sender"] is None
    finally:
        conn.close()
