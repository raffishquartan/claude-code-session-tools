from __future__ import annotations

import json

import pytest

from cc_session_tools.lib.pdata import importers
from cc_session_tools.lib.pdata.manifest import FieldSpec, ManifestEntry


def test_import_whole_file_one_row(tmp_path):
    (tmp_path / "note.md").write_text("hello world\n")
    entry = ManifestEntry(path="note.md", classification="db-owned",
                          record_group="notes", strategy="whole-file")
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 1
    assert rows[0].content == "hello world\n"
    assert rows[0].file_path is None
    assert rows[0].fields == {}


def test_import_delimited_sections_splits_on_default_heading(tmp_path):
    (tmp_path / "log.md").write_text("## first\nbody one\n## second\nbody two\n")
    entry = ManifestEntry(path="log.md", classification="db-owned",
                          record_group="log", strategy="delimited-sections")
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 2
    assert rows[0].content.startswith("## first")
    assert rows[1].content.startswith("## second")


def test_import_delimited_sections_custom_delimiter(tmp_path):
    (tmp_path / "log.md").write_text("# Snapshot A\nx\n# Snapshot B\ny\n")
    entry = ManifestEntry(
        path="log.md", classification="db-owned", record_group="log",
        strategy="delimited-sections", delimiter=r"(?m)^# .*$",
    )
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 2
    assert "Snapshot A" in rows[0].content
    assert "Snapshot B" in rows[1].content


def test_import_csv_rows_maps_fields_content_and_file_path(tmp_path):
    (tmp_path / "log.csv").write_text(
        "content,file_path,sender\nhi there,notes/a.pdf,bob\n"
    )
    entry = ManifestEntry(
        path="log.csv", classification="db-owned", record_group="log",
        strategy="csv-rows", content_column="content", file_path_column="file_path",
        fields=[FieldSpec(name="sender", sql_type="TEXT", column="sender")],
    )
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 1
    assert rows[0].content == "hi there"
    assert rows[0].file_path == "notes/a.pdf"
    assert rows[0].fields == {"sender": "bob"}


def test_import_csv_rows_without_content_column_serializes_row(tmp_path):
    (tmp_path / "ideas.csv").write_text("idea,priority\nfirst,1\n")
    entry = ManifestEntry(
        path="ideas.csv", classification="db-owned", record_group="ideas",
        strategy="csv-rows",
        fields=[FieldSpec(name="idea", sql_type="TEXT", column="idea"),
                FieldSpec(name="priority", sql_type="INTEGER", column="priority")],
    )
    rows = importers.import_entry(tmp_path, entry)
    parsed = json.loads(rows[0].content)
    assert parsed == {"idea": "first", "priority": "1"}
    assert rows[0].fields == {"idea": "first", "priority": "1"}


def test_import_json_array_rows(tmp_path):
    (tmp_path / "chars.json").write_text(json.dumps([{"name": "a"}, {"name": "b"}]))
    entry = ManifestEntry(
        path="chars.json", classification="db-owned", record_group="chars",
        strategy="json-array-rows",
        fields=[FieldSpec(name="name", sql_type="TEXT", column="name")],
    )
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 2
    assert rows[0].fields == {"name": "a"}
    assert rows[1].fields == {"name": "b"}


def test_import_json_singleton_one_row(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"count": 3}))
    entry = ManifestEntry(
        path="state.json", classification="db-owned", record_group="state",
        strategy="json-singleton",
        fields=[FieldSpec(name="count", sql_type="INTEGER", column="count")],
    )
    rows = importers.import_entry(tmp_path, entry)
    assert len(rows) == 1
    assert rows[0].fields == {"count": "3"}


def test_import_row_field_values_are_always_strings(tmp_path):
    """Plan Decision 7: field values are always strings, matching how `ccst pdata
    add --field k=v` already only ever sends strings."""
    (tmp_path / "state.json").write_text(json.dumps({"count": 3, "ratio": 1.5}))
    entry = ManifestEntry(
        path="state.json", classification="db-owned", record_group="state",
        strategy="json-singleton",
        fields=[FieldSpec(name="count", sql_type="INTEGER", column="count"),
                FieldSpec(name="ratio", sql_type="REAL", column="ratio")],
    )
    rows = importers.import_entry(tmp_path, entry)
    assert all(isinstance(v, str) for v in rows[0].fields.values())


def test_count_source_rows_matches_import_entry_for_every_strategy(tmp_path):
    """init_service's entry-count parity check (spec §7.1 step 4) relies on this
    being a genuinely independent re-count, not a re-read of import_entry's own
    result — but it must still agree with import_entry's row count on well-formed
    input."""
    (tmp_path / "ideas.csv").write_text("idea,priority\nfirst,1\nsecond,2\n")
    csv_entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                              record_group="ideas", strategy="csv-rows")
    assert importers.count_source_rows(tmp_path, csv_entry) == len(
        importers.import_entry(tmp_path, csv_entry)
    )

    (tmp_path / "chars.json").write_text(json.dumps([{"name": "a"}, {"name": "b"}]))
    json_entry = ManifestEntry(path="chars.json", classification="db-owned",
                               record_group="chars", strategy="json-array-rows")
    assert importers.count_source_rows(tmp_path, json_entry) == 2

    (tmp_path / "state.json").write_text(json.dumps({"count": 3}))
    singleton_entry = ManifestEntry(path="state.json", classification="db-owned",
                                    record_group="state", strategy="json-singleton")
    assert importers.count_source_rows(tmp_path, singleton_entry) == 1

    (tmp_path / "log.md").write_text("## first\nbody one\n## second\nbody two\n")
    sections_entry = ManifestEntry(path="log.md", classification="db-owned",
                                   record_group="log", strategy="delimited-sections")
    assert importers.count_source_rows(tmp_path, sections_entry) == 2


def test_import_json_array_rows_rejects_non_array_shape(tmp_path):
    """A hand-edited manifest entry (pm-project-init Step 4) can assign
    'json-array-rows' to a file that is actually a JSON object — must raise
    ValueError, not AttributeError from a bare list iteration/`.get()` call."""
    (tmp_path / "chars.json").write_text(json.dumps({"not": "a list"}))
    entry = ManifestEntry(path="chars.json", classification="db-owned",
                          record_group="chars", strategy="json-array-rows")
    with pytest.raises(ValueError, match="json-array-rows"):
        importers.import_entry(tmp_path, entry)


def test_import_json_array_rows_rejects_non_object_elements(tmp_path):
    """Same strategy, but the file is a JSON array of scalars, not objects."""
    (tmp_path / "chars.json").write_text(json.dumps(["not", "objects"]))
    entry = ManifestEntry(path="chars.json", classification="db-owned",
                          record_group="chars", strategy="json-array-rows")
    with pytest.raises(ValueError, match="json-array-rows"):
        importers.import_entry(tmp_path, entry)


def test_import_json_singleton_rejects_non_object_shape(tmp_path):
    """A hand-edited manifest entry can assign 'json-singleton' to a file that is
    actually a JSON array — must raise ValueError, not AttributeError from a bare
    `.get()` call on a list."""
    (tmp_path / "state.json").write_text(json.dumps(["a", "b"]))
    entry = ManifestEntry(path="state.json", classification="db-owned",
                          record_group="state", strategy="json-singleton")
    with pytest.raises(ValueError, match="json-singleton"):
        importers.import_entry(tmp_path, entry)
