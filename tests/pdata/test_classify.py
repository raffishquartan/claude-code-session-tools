from __future__ import annotations

import json

from cc_session_tools.lib.pdata import classify, naming


def test_binary_extension_classified_folder_owned(tmp_path):
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n")
    entries = classify.walk_and_classify(tmp_path)
    assert entries == [
        classify.ManifestEntry(path="photo.png", classification="folder-owned")
    ]


def test_markdown_defaults_folder_owned_not_guessed(tmp_path):
    """The classifier must NOT try to decide whether a .md file is a log or a
    versioned doc — that judgement call is out of this plan's scope (see plan
    Decision 5)."""
    (tmp_path / "ccst-ideas.md").write_text("## idea one\nbody\n## idea two\nbody\n")
    entries = classify.walk_and_classify(tmp_path)
    assert entries == [
        classify.ManifestEntry(path="ccst-ideas.md", classification="folder-owned")
    ]


def test_csv_classified_db_owned_with_fields_from_header(tmp_path):
    (tmp_path / "ideas.csv").write_text("idea,priority\nfirst,1\nsecond,2\n")
    entries = classify.walk_and_classify(tmp_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.classification == "db-owned"
    assert entry.strategy == "csv-rows"
    assert entry.record_group == "ideas"
    assert {f.name for f in entry.fields} == {"idea", "priority"}


def test_csv_content_and_file_path_columns_recognized(tmp_path):
    (tmp_path / "log.csv").write_text("content,file_path,sender\nhi,a.pdf,bob\n")
    entry = classify.walk_and_classify(tmp_path)[0]
    assert entry.content_column == "content"
    assert entry.file_path_column == "file_path"
    assert {f.name for f in entry.fields} == {"sender"}


def test_json_object_classified_singleton(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"last_shop": "2026-01-01", "count": 3}))
    entry = classify.walk_and_classify(tmp_path)[0]
    assert entry.classification == "db-owned"
    assert entry.strategy == "json-singleton"
    assert entry.record_group == "state"
    field_types = {f.name: f.sql_type for f in entry.fields}
    assert field_types["last_shop"] == "TEXT"
    assert field_types["count"] == "INTEGER"


def test_json_array_of_objects_classified_array_rows(tmp_path):
    (tmp_path / "chars.json").write_text(json.dumps([{"name": "a"}, {"name": "b"}]))
    entry = classify.walk_and_classify(tmp_path)[0]
    assert entry.strategy == "json-array-rows"
    assert {f.name for f in entry.fields} == {"name"}


def test_unparsable_json_falls_back_to_folder_owned(tmp_path):
    (tmp_path / "broken.json").write_text("{not json")
    entry = classify.walk_and_classify(tmp_path)[0]
    assert entry.classification == "folder-owned"


def test_excluded_directories_are_skipped(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("junk")
    (tmp_path / "cc-sessions").mkdir()
    (tmp_path / "cc-sessions" / "notes.md").write_text("junk")
    (tmp_path / "CLAUDE.md").write_text("# demo\n")
    entries = classify.walk_and_classify(tmp_path)
    assert [e.path for e in entries] == ["CLAUDE.md"]


def test_entries_sorted_by_relative_path(tmp_path):
    (tmp_path / "b.csv").write_text("x\n1\n")
    (tmp_path / "a.csv").write_text("x\n1\n")
    entries = classify.walk_and_classify(tmp_path)
    assert [e.path for e in entries] == ["a.csv", "b.csv"]


def test_colliding_basenames_in_different_subdirs_get_disambiguated_record_groups(tmp_path):
    """_default_record_group derives its proposal from path.stem alone, so
    a/notes.csv and b/notes.csv would otherwise both propose record_group=notes —
    silently merging two unrelated files' rows into one shared group at --write
    time with no error. walk_and_classify must detect the collision across the
    whole manifest and disambiguate every colliding entry."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "notes.csv").write_text("x\n1\n")
    (tmp_path / "b" / "notes.csv").write_text("x\n2\n")
    entries = classify.walk_and_classify(tmp_path)
    groups = {e.path: e.record_group for e in entries}
    assert len(set(groups.values())) == 2, groups


def test_existing_record_group_forces_disambiguation_even_for_a_single_file(tmp_path):
    """A record_group can already be live and populated — from an earlier ccst
    pdata init run, or from an unrelated mechanism entirely (e.g. Plan C's
    session-output groups) — even when only one new file in this pass proposes
    that name. walk_and_classify must never silently propose merging into it."""
    (tmp_path / "notes.csv").write_text("x\n1\n")
    entries = classify.walk_and_classify(
        tmp_path, existing_record_groups=frozenset({"notes"})
    )
    assert entries[0].record_group != "notes"


def test_csv_header_colliding_with_reserved_base_column_is_renamed(tmp_path):
    """A header literally named `version`/`id`/`created_at`/etc. would otherwise
    pass classification silently and only fail inside schema_add_field at
    --write time — past the human-review step the spec relies on. classify.py
    must rename it to a non-reserved, still-valid field name up front so the
    dry-run report already shows the name that will actually be used."""
    (tmp_path / "docs.csv").write_text("version,note\n1.0,first\n")
    entry = classify.walk_and_classify(tmp_path)[0]
    field_names = {f.name for f in entry.fields}
    assert "version" not in field_names
    for name in field_names:
        naming.validate_field_name(name)  # must not raise
