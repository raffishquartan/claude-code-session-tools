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


def test_schema_list_and_show(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.add_record(
        project="testproj", record_group="filings", content="x", file_path=None,
        fields={}, created_at=1000,
    )
    service.schema_add_field(
        project="testproj", record_group="filings", field_name="doc_type",
        sql_type="TEXT", description="kind of document", default=None,
    )

    groups = service.schema_list(project="testproj")
    names = {g["record_group"] for g in groups}
    assert "filings" in names

    columns = service.schema_show(project="testproj", record_group="filings")
    base_names = {c["name"] for c in columns if c["source"] == "base"}
    ext_names = {c["name"]: c for c in columns if c["source"] == "extension"}
    assert base_names == {"id", "record_group", "content", "file_path",
                           "created_at", "updated_at", "version", "deleted_at"}
    assert ext_names["doc_type"]["type"] == "TEXT"
    assert ext_names["doc_type"]["description"] == "kind of document"


def test_schema_show_field_without_description_shows_blank(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="filings", field_name="doc_type",
        sql_type="TEXT", description=None, default=None,
    )
    columns = service.schema_show(project="testproj", record_group="filings")
    ext = next(c for c in columns if c["name"] == "doc_type")
    assert ext["description"] is None


def test_add_record_routes_field_to_extension_table(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description=None, default=None,
    )
    record = service.add_record(
        project="testproj", record_group="key-events", content="an event",
        file_path=None, fields={"sender": "alice"}, created_at=1000,
    )
    assert record.fields == {"sender": "alice"}


def test_add_record_rejects_unregistered_field(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="unregistered field"):
        service.add_record(
            project="testproj", record_group="key-events", content="an event",
            file_path=None, fields={"nope": "x"}, created_at=1000,
        )


def test_add_record_with_no_fields_and_no_extension_table_stays_base_only(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    record = service.add_record(
        project="testproj", record_group="ccst-ideas", content="an idea",
        file_path=None, fields={}, created_at=1000,
    )
    assert record.fields == {}


def test_get_record_flattens_extension_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description=None, default=None,
    )
    created = service.add_record(
        project="testproj", record_group="key-events", content="an event",
        file_path=None, fields={"sender": "alice"}, created_at=1000,
    )
    fetched = service.get_record(project="testproj", record_id=created.id)
    assert fetched is not None
    assert fetched.content == "an event"
    assert fetched.fields == {"sender": "alice"}


def test_get_record_returns_none_for_missing_id(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    assert service.get_record(project="testproj", record_id=999) is None


def test_list_records_flattens_extension_fields_for_every_row(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description=None, default=None,
    )
    service.add_record(project="testproj", record_group="key-events", content="e1",
                        file_path=None, fields={"sender": "alice"}, created_at=1000)
    service.add_record(project="testproj", record_group="key-events", content="e2",
                        file_path=None, fields={"sender": "bob"}, created_at=2000)
    rows = service.list_records(project="testproj", record_group="key-events")
    assert [r.fields["sender"] for r in rows] == ["alice", "bob"]


def test_query_records_service_layer(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sent_at",
        sql_type="INTEGER", description=None, default=None,
    )
    # fields values are always strings (matching every --field k=v caller — the CLI can only
    # ever produce strings, and add_record's fields: Mapping[str, str] signature reflects
    # that). SQLite's own column affinity converts a bound TEXT '100' into the stored/compared
    # INTEGER 100 for an INTEGER-affinity column — confirmed by direct execution — so this
    # doesn't need Python-side int parsing anywhere in this plan.
    service.add_record(project="testproj", record_group="key-events", content="a",
                        file_path=None, fields={"sent_at": "100"}, created_at=1)
    service.add_record(project="testproj", record_group="key-events", content="b",
                        file_path=None, fields={"sent_at": "200"}, created_at=2)
    rows = service.query_records(
        project="testproj", record_group="key-events", where=["sent_at > 150"],
    )
    assert [r.content for r in rows] == ["b"]


def test_query_records_rejects_bad_where_syntax(monkeypatch, tmp_path):
    # Must be too few whitespace-separated tokens to match _WHERE_CLAUSE_RE at all (a single
    # word, with no op/value) so this actually exercises the "malformed clause" branch — not
    # the "invalid operator" branch already covered by test_query_records_rejects_bad_where_operator.
    # ("not a valid clause" would parse as field="not", op="A", value="valid clause" and raise
    # the invalid-operator error instead, since "A" is not a valid op — that string looked like
    # a syntax failure but wasn't one.)
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="malformed"):
        service.query_records(
            project="testproj", record_group="key-events", where=["singleword"],
        )


def test_query_records_rejects_bad_where_operator(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="operator"):
        service.query_records(
            project="testproj", record_group="key-events", where=["content ~= x"],
        )

# Note: query's --include-deleted default-exclusion behaviour is regression-tested in Task 16
# (test_query_records_excludes_soft_deleted_by_default / test_pdata_query_include_deleted),
# not here — it depends on service.delete_record, which doesn't exist until Task 16, matching
# this plan's existing precedent of deferring a test to the task that provides its dependency
# (see test_get_record_excludes_soft_deleted_by_default's "moved here from Task 12" note).


def test_update_record_happy_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.schema_add_field(
        project="testproj", record_group="key-events", field_name="sender",
        sql_type="TEXT", description=None, default=None,
    )
    created = service.add_record(
        project="testproj", record_group="key-events", content="old",
        file_path=None, fields={"sender": "alice"}, created_at=1,
    )
    updated = service.update_record(
        project="testproj", record_id=created.id, expected_version=1,
        content="new", file_path=None, fields={"sender": "bob"}, updated_at=2,
    )
    assert updated.content == "new"
    assert updated.version == 2
    assert updated.fields["sender"] == "bob"


def test_update_record_conflict_raises_with_current_and_attempted(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="old",
        file_path=None, fields={}, created_at=1,
    )
    with pytest.raises(service.VersionConflictError) as exc_info:
        service.update_record(
            project="testproj", record_id=created.id, expected_version=99,
            content="new", file_path=None, fields={}, updated_at=2,
        )
    assert exc_info.value.current["content"] == "old"
    assert exc_info.value.attempted["content"] == "new"


def test_update_record_missing_id_raises_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(service.RecordNotFoundError):
        service.update_record(
            project="testproj", record_id=999, expected_version=1,
            content="new", file_path=None, fields={}, updated_at=2,
        )


def test_update_record_omitting_file_preserves_existing_file_path(monkeypatch, tmp_path):
    """Regression test: a content-only update (--file omitted, i.e. file_path=None) must not
    silently null out a previously-set file_path."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="filings", content="old",
        file_path="filings/original.md", fields={}, created_at=1,
    )
    updated = service.update_record(
        project="testproj", record_id=created.id, expected_version=1,
        content="new", file_path=None, fields={}, updated_at=2,
    )
    assert updated.content == "new"
    assert updated.file_path == "filings/original.md"


def test_update_record_omitting_content_updates_only_file_path(monkeypatch, tmp_path):
    """Regression test: --content is optional per spec §5 — a file-only (or field-only) update
    must not require resending the existing content."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="filings", content="original content",
        file_path="filings/old.md", fields={}, created_at=1,
    )
    updated = service.update_record(
        project="testproj", record_id=created.id, expected_version=1,
        content=None, file_path="filings/new.md", fields={}, updated_at=2,
    )
    assert updated.content == "original content"
    assert updated.file_path == "filings/new.md"


def test_update_record_rejects_empty_update(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="x",
        file_path=None, fields={}, created_at=1,
    )
    with pytest.raises(ValueError, match="at least one"):
        service.update_record(
            project="testproj", record_id=created.id, expected_version=1,
            content=None, file_path=None, fields={}, updated_at=2,
        )


def test_get_record_excludes_soft_deleted_by_default(monkeypatch, tmp_path):
    """Moved here from Task 12: depends on delete_record, added in this task."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="x", file_path=None,
        fields={}, created_at=1000,
    )
    service.delete_record(project="testproj", record_id=created.id, expected_version=1)
    assert service.get_record(project="testproj", record_id=created.id) is None
    assert service.get_record(
        project="testproj", record_id=created.id, include_deleted=True,
    ) is not None


def test_query_records_excludes_soft_deleted_by_default(monkeypatch, tmp_path):
    """Moved here from Task 14: depends on delete_record, added in this task. Regression test
    for query's --include-deleted default-exclude contract (spec §4.5: 'list/query/get exclude
    soft-deleted rows by default; --include-deleted shows them') — query is not exempt from
    that default just because it filters on --where instead of --since/--until."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="x", file_path=None,
        fields={}, created_at=1,
    )
    service.delete_record(project="testproj", record_id=created.id, expected_version=1)

    visible = service.query_records(
        project="testproj", record_group="notes", where=["content = x"],
    )
    assert visible == []

    visible_with_deleted = service.query_records(
        project="testproj", record_group="notes", where=["content = x"],
        include_deleted=True,
    )
    assert [r.id for r in visible_with_deleted] == [created.id]


def test_delete_record_conflict(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="x", file_path=None,
        fields={}, created_at=1000,
    )
    with pytest.raises(service.VersionConflictError):
        service.delete_record(project="testproj", record_id=created.id, expected_version=99)


def test_restore_record_makes_it_visible_again(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    created = service.add_record(
        project="testproj", record_group="notes", content="x", file_path=None,
        fields={}, created_at=1000,
    )
    service.delete_record(project="testproj", record_id=created.id, expected_version=1)
    service.restore_record(project="testproj", record_id=created.id)
    assert service.get_record(project="testproj", record_id=created.id) is not None


def test_find_records_by_file_path_prefix_matches_across_groups(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.add_record(
        project="demo", record_group="letters", content="a",
        file_path="correspondence/a.md", fields={}, created_at=1,
    )
    service.add_record(
        project="demo", record_group="notes", content="b",
        file_path="correspondence/b.md", fields={}, created_at=1,
    )
    service.add_record(
        project="demo", record_group="letters", content="c",
        file_path="analysis/c.md", fields={}, created_at=1,
    )

    matches = service.find_records_by_file_path_prefix(project="demo", prefix="correspondence/")

    assert sorted(r.file_path for r in matches) == ["correspondence/a.md", "correspondence/b.md"]


def test_find_records_by_file_path_prefix_empty_project_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    # no records ever added for "demo" - no .db file exists at all yet
    assert service.find_records_by_file_path_prefix(project="demo", prefix="correspondence/") == []


def test_find_records_by_file_path_prefix_rejects_empty_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="non-empty prefix"):
        service.find_records_by_file_path_prefix(project="demo", prefix="")


def test_find_records_by_file_path_prefix_preserves_leading_whitespace(monkeypatch, tmp_path):
    """Regression test: querying via repository.query_records() with an already-parsed
    condition tuple (not this module's own string-DSL query_records()) means a prefix
    starting with whitespace isn't silently stripped by _parse_where_clause's \\s+ regex."""
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    service.add_record(
        project="demo", record_group="letters", content="a",
        file_path=" leading-space/a.md", fields={}, created_at=1,
    )
    service.add_record(
        project="demo", record_group="letters", content="b",
        file_path="leading-space/b.md", fields={}, created_at=1,
    )

    matches = service.find_records_by_file_path_prefix(project="demo", prefix=" leading-space/")

    assert [r.file_path for r in matches] == [" leading-space/a.md"]
