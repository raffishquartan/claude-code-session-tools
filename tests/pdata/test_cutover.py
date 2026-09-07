from __future__ import annotations

from cc_session_tools.lib.pdata import cutover
from cc_session_tools.lib.pdata.init_paths import (
    MIGRATED_ARCHIVE_DIRNAME,
    MIGRATED_MANIFEST_FILENAME,
)
from cc_session_tools.lib.pdata.manifest import FieldSpec, ManifestEntry


def test_archive_entries_moves_files_and_writes_manifest_log(tmp_path):
    (tmp_path / "ideas.csv").write_text("idea\nfirst\n")
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                          record_group="ideas", strategy="csv-rows")

    cutover.archive_entries(project_root=tmp_path, entries=[entry], project="demo")

    assert not (tmp_path / "ideas.csv").exists()
    archived = tmp_path / MIGRATED_ARCHIVE_DIRNAME / "ideas.csv"
    assert archived.exists()
    assert archived.read_text() == "idea\nfirst\n"

    log_path = tmp_path / MIGRATED_ARCHIVE_DIRNAME / MIGRATED_MANIFEST_FILENAME
    assert log_path.exists()
    assert "ideas.csv" in log_path.read_text()
    assert "ideas" in log_path.read_text()  # record_group name recorded


def test_archive_entries_preserves_relative_directory_structure(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "log.csv").write_text("x\n1\n")
    entry = ManifestEntry(path="sub/log.csv", classification="db-owned",
                          record_group="sublog", strategy="csv-rows")

    cutover.archive_entries(project_root=tmp_path, entries=[entry], project="demo")

    assert (tmp_path / MIGRATED_ARCHIVE_DIRNAME / "sub" / "log.csv").exists()


def test_archive_entries_noop_for_empty_list(tmp_path):
    cutover.archive_entries(project_root=tmp_path, entries=[], project="demo")
    assert not (tmp_path / MIGRATED_ARCHIVE_DIRNAME).exists()


def test_archive_entries_appends_across_calls(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "b.csv").write_text("x\n1\n")
    entry_a = ManifestEntry(path="a.csv", classification="db-owned",
                            record_group="a", strategy="csv-rows")
    entry_b = ManifestEntry(path="b.csv", classification="db-owned",
                            record_group="b", strategy="csv-rows")

    cutover.archive_entries(project_root=tmp_path, entries=[entry_a], project="demo")
    cutover.archive_entries(project_root=tmp_path, entries=[entry_b], project="demo")

    log_path = tmp_path / MIGRATED_ARCHIVE_DIRNAME / MIGRATED_MANIFEST_FILENAME
    text = log_path.read_text()
    assert "a.csv" in text and "b.csv" in text


def test_archive_entries_writes_pointer_file_by_default(tmp_path):
    (tmp_path / "ideas.csv").write_text("idea\nfirst\n")
    entry = ManifestEntry(
        path="ideas.csv", classification="db-owned", record_group="ideas",
        strategy="csv-rows",
        fields=[FieldSpec(name="idea", sql_type="TEXT", description="the idea text")],
    )

    cutover.archive_entries(project_root=tmp_path, entries=[entry], project="demo")

    pointer = tmp_path / "ideas.md"
    assert pointer.exists()
    content = pointer.read_text()
    assert "ideas" in content  # record_group
    assert "idea" in content and "TEXT" in content  # schema table
    assert "the idea text" in content  # field description
    assert "ccst pdata list --project demo --group ideas" in content  # example query


def test_archive_entries_pointer_file_includes_preface_text_when_set(tmp_path):
    (tmp_path / "garbled.csv").write_text("idea\nfirst\n")
    entry = ManifestEntry(
        path="garbled.csv", classification="db-owned", record_group="garbled",
        strategy="csv-rows", preface_text="# generated 2026-08-12, do not edit by hand\n",
    )

    cutover.archive_entries(project_root=tmp_path, entries=[entry], project="demo")

    content = (tmp_path / "garbled.md").read_text()
    assert "generated 2026-08-12, do not edit by hand" in content
    assert "Preserved preface text" in content


def test_archive_entries_pointer_file_omits_preface_heading_when_unset(tmp_path):
    (tmp_path / "clean.csv").write_text("idea\nfirst\n")
    entry = ManifestEntry(path="clean.csv", classification="db-owned",
                          record_group="clean", strategy="csv-rows")

    cutover.archive_entries(project_root=tmp_path, entries=[entry], project="demo")

    content = (tmp_path / "clean.md").read_text()
    assert "Preserved preface text" not in content


def test_archive_entries_leave_no_pointer_files_suppresses_pointer(tmp_path):
    (tmp_path / "ideas.csv").write_text("idea\nfirst\n")
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                          record_group="ideas", strategy="csv-rows")

    cutover.archive_entries(
        project_root=tmp_path, entries=[entry], project="demo",
        write_pointer_files=False,
    )

    assert not (tmp_path / "ideas.md").exists()
