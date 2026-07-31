from __future__ import annotations

from cc_session_tools.lib.pdata import cutover
from cc_session_tools.lib.pdata.init_paths import (
    MIGRATED_ARCHIVE_DIRNAME,
    MIGRATED_MANIFEST_FILENAME,
)
from cc_session_tools.lib.pdata.manifest import ManifestEntry


def test_archive_entries_moves_files_and_writes_manifest_log(tmp_path):
    (tmp_path / "ideas.csv").write_text("idea\nfirst\n")
    entry = ManifestEntry(path="ideas.csv", classification="db-owned",
                          record_group="ideas", strategy="csv-rows")

    cutover.archive_entries(project_root=tmp_path, entries=[entry])

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

    cutover.archive_entries(project_root=tmp_path, entries=[entry])

    assert (tmp_path / MIGRATED_ARCHIVE_DIRNAME / "sub" / "log.csv").exists()


def test_archive_entries_noop_for_empty_list(tmp_path):
    cutover.archive_entries(project_root=tmp_path, entries=[])
    assert not (tmp_path / MIGRATED_ARCHIVE_DIRNAME).exists()


def test_archive_entries_appends_across_calls(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "b.csv").write_text("x\n1\n")
    entry_a = ManifestEntry(path="a.csv", classification="db-owned",
                            record_group="a", strategy="csv-rows")
    entry_b = ManifestEntry(path="b.csv", classification="db-owned",
                            record_group="b", strategy="csv-rows")

    cutover.archive_entries(project_root=tmp_path, entries=[entry_a])
    cutover.archive_entries(project_root=tmp_path, entries=[entry_b])

    log_path = tmp_path / MIGRATED_ARCHIVE_DIRNAME / MIGRATED_MANIFEST_FILENAME
    text = log_path.read_text()
    assert "a.csv" in text and "b.csv" in text
