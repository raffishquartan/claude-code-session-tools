from __future__ import annotations

import json

import pytest

from cc_session_tools.lib.pdata import manifest


def test_manifest_entry_folder_owned_needs_no_group():
    entry = manifest.ManifestEntry(path="CLAUDE.md", classification="folder-owned")
    assert entry.record_group is None


def test_manifest_entry_db_owned_requires_record_group_and_strategy():
    with pytest.raises(ValueError, match="record_group"):
        manifest.ManifestEntry(path="ideas.csv", classification="db-owned")


def test_manifest_entry_db_owned_requires_valid_strategy():
    with pytest.raises(ValueError, match="strategy"):
        manifest.ManifestEntry(
            path="ideas.csv", classification="db-owned", record_group="ideas",
            strategy="not-a-real-strategy",
        )


def test_manifest_entry_rejects_invalid_classification():
    with pytest.raises(ValueError, match="classification"):
        manifest.ManifestEntry(path="x", classification="bogus")


def test_db_group_and_db_strategy_accessors():
    entry = manifest.ManifestEntry(
        path="ideas.csv", classification="db-owned", record_group="ideas",
        strategy="csv-rows",
    )
    assert entry.db_group() == "ideas"
    assert entry.db_strategy() == "csv-rows"


def test_db_group_asserts_on_folder_owned_entry():
    entry = manifest.ManifestEntry(path="CLAUDE.md", classification="folder-owned")
    with pytest.raises(AssertionError):
        entry.db_group()


def test_manifest_rejects_duplicate_entry_paths():
    """init_service.write()/_verify() key their per-entry bookkeeping by entry.path
    (`entry_rows: dict[str, list[tuple[int, ImportRow]]]`), and
    cutover.archive_entries renames each entry's source file by path once — two
    entries sharing a path would silently overwrite each other's tracked rows and
    then raise an unhandled FileNotFoundError on the second rename attempt, well
    after backup/verification already reported success. Reject the collision at
    construction so it can never reach write()."""
    with pytest.raises(ValueError, match="duplicate"):
        manifest.Manifest(
            project="demo",
            entries=[
                manifest.ManifestEntry(path="ideas.csv", classification="folder-owned"),
                manifest.ManifestEntry(path="ideas.csv", classification="folder-owned"),
            ],
        )


def test_save_then_load_round_trips(tmp_path):
    field = manifest.FieldSpec(name="priority", sql_type="INTEGER", column="priority")
    entries = [
        manifest.ManifestEntry(path="CLAUDE.md", classification="folder-owned"),
        manifest.ManifestEntry(
            path="ideas.csv", classification="db-owned", record_group="ideas",
            strategy="csv-rows", content_column="idea", fields=[field],
        ),
    ]
    m = manifest.Manifest(project="demo", entries=entries)
    path = tmp_path / "proposal.json"
    manifest.save(m, path)

    loaded = manifest.load(path)
    assert loaded.project == "demo"
    assert len(loaded.entries) == 2
    assert loaded.entries[1].fields[0].name == "priority"
    assert loaded.entries[1].fields[0].sql_type == "INTEGER"


def test_load_raises_value_error_on_missing_required_key(tmp_path):
    """A hand-edited proposal missing "project" must raise ValueError (caught by
    _cmd_pdata_init's exit-2 path), never an uncaught KeyError."""
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps({"entries": [{"path": "x", "classification": "folder-owned"}]}))
    with pytest.raises(ValueError, match="malformed manifest"):
        manifest.load(path)


def test_load_raises_value_error_on_non_dict_entry(tmp_path):
    """A hand-edited proposal whose "entries" contains a non-object element must
    raise ValueError, never an uncaught AttributeError/TypeError."""
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps({"project": "demo", "entries": ["not-an-object"]}))
    with pytest.raises(ValueError, match="malformed manifest"):
        manifest.load(path)
