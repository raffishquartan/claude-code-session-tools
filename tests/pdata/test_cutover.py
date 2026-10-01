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


# --- pointer-path collisions (spec pdata/init-pointer-files) -------------------------------

import os  # noqa: E402

import pytest  # noqa: E402

REAL = "REAL NARRATIVE - MUST SURVIVE\n"


def _entry(path: str, group: str = "things", preface: str | None = None) -> ManifestEntry:
    return ManifestEntry(
        path=path, classification="db-owned", record_group=group, strategy="csv-rows",
        fields=[FieldSpec(name="idea", sql_type="TEXT")], preface_text=preface,
    )


def _project(tmp_path, *names: str):
    for name in names:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("idea\nfirst\n")
    return tmp_path


def _log(tmp_path) -> str:
    return (tmp_path / MIGRATED_ARCHIVE_DIRNAME / MIGRATED_MANIFEST_FILENAME).read_text()


def _pointer_files(tmp_path):
    return sorted(p.name for p in tmp_path.rglob("*.pdata-pointer.md"))


def test_real_md_beside_the_csv_survives_and_pointer_is_substituted(tmp_path):
    _project(tmp_path, "data/things.csv")
    (tmp_path / "data/things.md").write_text(REAL)
    entry = _entry("data/things.csv")

    outcomes = cutover.archive_entries(project_root=tmp_path, entries=[entry], project="demo")

    assert (tmp_path / "data/things.md").read_text() == REAL
    pointer = tmp_path / "data/things.csv.pdata-pointer.md"
    assert pointer.read_text() == cutover._pointer_file_content(project="demo", entry=entry)
    assert "| idea | TEXT |" in pointer.read_text()
    assert "ccst pdata list --project demo --group things" in pointer.read_text()
    assert [(o.entry_path, o.pointer_path, o.occupied) for o in outcomes] == [
        ("data/things.csv", "data/things.csv.pdata-pointer.md", ("data/things.md",))
    ]
    assert "pointer substituted" in _log(tmp_path)


def test_no_collision_writes_the_exact_pointer_at_the_md_path(tmp_path):
    _project(tmp_path, "data/things.csv")
    entry = _entry("data/things.csv")

    outcomes = cutover.archive_entries(project_root=tmp_path, entries=[entry], project="demo")

    assert (tmp_path / "data/things.md").read_text() == cutover._pointer_file_content(
        project="demo", entry=entry)
    assert [(o.pointer_path, o.occupied) for o in outcomes] == [("data/things.md", ())]
    assert "pointer" not in _log(tmp_path)
    assert _pointer_files(tmp_path) == []


def test_substitute_also_occupied_skips_the_pointer_but_archives_the_data(tmp_path):
    _project(tmp_path, "data/things.csv")
    (tmp_path / "data/things.md").write_text(REAL)
    (tmp_path / "data/things.csv.pdata-pointer.md").write_text("someone else's\n")
    seen = []

    outcomes = cutover.archive_entries(
        project_root=tmp_path, entries=[_entry("data/things.csv")], project="demo",
        on_pointer=seen.append,
    )

    assert (tmp_path / "data/things.md").read_text() == REAL
    assert (tmp_path / "data/things.csv.pdata-pointer.md").read_text() == "someone else's\n"
    assert (tmp_path / MIGRATED_ARCHIVE_DIRNAME / "data/things.csv").exists()
    assert outcomes[0].pointer_path is None
    assert outcomes[0].occupied == ("data/things.md", "data/things.csv.pdata-pointer.md")
    assert seen == outcomes
    assert "pointer skipped" in _log(tmp_path)


def test_csv_and_json_sharing_a_stem_with_a_real_md_each_get_a_pointer(tmp_path):
    _project(tmp_path, "data/a.csv", "data/a.json")
    (tmp_path / "data/a.md").write_text(REAL)

    cutover.archive_entries(
        project_root=tmp_path, entries=[_entry("data/a.csv", "a"), _entry("data/a.json", "b")],
        project="demo",
    )

    assert (tmp_path / "data/a.md").read_text() == REAL
    assert _pointer_files(tmp_path) == ["a.csv.pdata-pointer.md", "a.json.pdata-pointer.md"]


def test_csv_and_json_sharing_a_stem_without_a_real_md(tmp_path):
    _project(tmp_path, "data/a.csv", "data/a.json")
    first, second = _entry("data/a.csv", "a"), _entry("data/a.json", "b")

    outcomes = cutover.archive_entries(
        project_root=tmp_path, entries=[first, second], project="demo")

    assert (tmp_path / "data/a.md").read_text() == cutover._pointer_file_content(
        project="demo", entry=first)
    assert (tmp_path / "data/a.json.pdata-pointer.md").read_text() == cutover._pointer_file_content(
        project="demo", entry=second)
    assert [o.pointer_path for o in outcomes] == ["data/a.md", "data/a.json.pdata-pointer.md"]


def test_plan_pointers_matches_what_archive_entries_does(tmp_path):
    _project(tmp_path, "data/a.csv", "data/a.json")
    (tmp_path / "data/a.json.pdata-pointer.md").write_text("x\n")
    entries = [_entry("data/a.csv", "a"), _entry("data/a.json", "b")]

    plans = cutover.plan_pointers(tmp_path, entries)
    outcomes = cutover.archive_entries(project_root=tmp_path, entries=entries, project="demo")

    assert plans == outcomes


def test_planning_modifies_nothing(tmp_path):
    _project(tmp_path, "data/things.csv")
    (tmp_path / "data/things.md").write_text(REAL)
    before = sorted(str(p) for p in tmp_path.rglob("*"))

    cutover.plan_pointers(tmp_path, [_entry("data/things.csv")])

    assert sorted(str(p) for p in tmp_path.rglob("*")) == before
    assert (tmp_path / "data/things.md").read_text() == REAL


@pytest.mark.parametrize("path", ["data/v1.2.csv", "things.csv", "data/sub/deep/x.csv"])
def test_substitute_name_for_dotted_and_nested_paths(tmp_path, path):
    _project(tmp_path, path)
    primary = tmp_path / path
    primary.with_suffix(".md").write_text(REAL)

    outcomes = cutover.archive_entries(project_root=tmp_path, entries=[_entry(path)], project="d")

    assert primary.with_suffix(".md").read_text() == REAL
    assert outcomes[0].pointer_path == f"{path}.pdata-pointer.md"
    assert (tmp_path / outcomes[0].pointer_path).exists()


def test_a_directory_at_the_pointer_path_is_left_alone(tmp_path):
    _project(tmp_path, "data/things.csv")
    (tmp_path / "data/things.md").mkdir()

    outcomes = cutover.archive_entries(
        project_root=tmp_path, entries=[_entry("data/things.csv")], project="demo")

    assert (tmp_path / "data/things.md").is_dir()
    assert outcomes[0].pointer_path == "data/things.csv.pdata-pointer.md"


def test_a_non_utf8_file_at_the_pointer_path_is_left_alone(tmp_path):
    _project(tmp_path, "data/things.csv")
    (tmp_path / "data/things.md").write_bytes(b"\xff\xfe\x00binary\x80")

    outcomes = cutover.archive_entries(
        project_root=tmp_path, entries=[_entry("data/things.csv")], project="demo")

    assert (tmp_path / "data/things.md").read_bytes() == b"\xff\xfe\x00binary\x80"
    assert outcomes[0].pointer_path == "data/things.csv.pdata-pointer.md"


def test_a_dangling_symlink_at_the_pointer_path_is_left_alone(tmp_path):
    _project(tmp_path, "data/things.csv")
    os.symlink(tmp_path / "nowhere", tmp_path / "data/things.md")

    outcomes = cutover.archive_entries(
        project_root=tmp_path, entries=[_entry("data/things.csv")], project="demo")

    assert os.path.islink(tmp_path / "data/things.md")
    assert not (tmp_path / "nowhere").exists()
    assert outcomes[0].pointer_path == "data/things.csv.pdata-pointer.md"


def test_a_live_symlink_to_a_lookalike_pointer_is_not_written_through(tmp_path):
    _project(tmp_path, "data/things.csv")
    entry = _entry("data/things.csv")
    target = tmp_path / "elsewhere.md"
    target.write_text(cutover._pointer_file_content(project="demo", entry=entry))
    original = target.read_text()
    os.symlink(target, tmp_path / "data/things.md")

    outcomes = cutover.archive_entries(project_root=tmp_path, entries=[entry], project="demo")

    assert target.read_text() == original
    assert os.path.islink(tmp_path / "data/things.md")
    assert outcomes[0].pointer_path == "data/things.csv.pdata-pointer.md"


def test_a_file_appearing_between_planning_and_writing_degrades_to_a_skip(tmp_path, monkeypatch):
    _project(tmp_path, "data/things.csv")
    (tmp_path / "data/things.md").write_text(REAL)
    real_plan = cutover.plan_pointers

    def plan_then_race(project_root, entries):
        plans = real_plan(project_root, entries)
        (tmp_path / "data/things.csv.pdata-pointer.md").write_text("raced in\n")
        return plans

    monkeypatch.setattr(cutover, "plan_pointers", plan_then_race)

    outcomes = cutover.archive_entries(
        project_root=tmp_path, entries=[_entry("data/things.csv")], project="demo")

    assert (tmp_path / "data/things.csv.pdata-pointer.md").read_text() == "raced in\n"
    assert (tmp_path / "data/things.md").read_text() == REAL
    assert outcomes[0].pointer_path is None
    assert "pointer skipped" in _log(tmp_path)


def _own_pointer(entry: ManifestEntry) -> str:
    return cutover._pointer_file_content(project="demo", entry=entry)


@pytest.mark.parametrize("variant", ["plain", "crlf", "bom"])
def test_the_entrys_own_earlier_pointer_is_rewritten_in_place(tmp_path, variant):
    _project(tmp_path, "data/things.csv")
    old = _own_pointer(_entry("data/things.csv"))
    text = {"plain": old, "crlf": old.replace("\n", "\r\n"), "bom": "﻿" + old}[variant]
    (tmp_path / "data/things.md").write_bytes(text.encode("utf-8"))
    entry = _entry("data/things.csv", preface="kept note")

    outcomes = cutover.archive_entries(project_root=tmp_path, entries=[entry], project="demo")

    assert "kept note" in (tmp_path / "data/things.md").read_text()
    assert _pointer_files(tmp_path) == []
    assert outcomes[0].pointer_path == "data/things.md" and outcomes[0].occupied == ()


def test_a_hand_edited_pointer_is_preserved(tmp_path):
    _project(tmp_path, "data/things.csv")
    lines = _own_pointer(_entry("data/things.csv")).split("\n")
    lines[2] = "My own notes about this file, not the generated line."
    edited = "\n".join(lines)
    (tmp_path / "data/things.md").write_text(edited)

    outcomes = cutover.archive_entries(
        project_root=tmp_path, entries=[_entry("data/things.csv")], project="demo")

    assert (tmp_path / "data/things.md").read_text() == edited
    assert outcomes[0].pointer_path == "data/things.csv.pdata-pointer.md"


def test_another_entrys_pointer_is_not_this_entrys_own(tmp_path):
    _project(tmp_path, "data/a.json")
    (tmp_path / "data/a.md").write_text(_own_pointer(_entry("data/a.csv")))

    outcomes = cutover.archive_entries(
        project_root=tmp_path, entries=[_entry("data/a.json")], project="demo")

    assert outcomes[0].pointer_path == "data/a.json.pdata-pointer.md"


def test_an_entry_whose_source_is_itself_a_md_is_not_a_collision(tmp_path):
    _project(tmp_path, "notes.md")
    entry = _entry("notes.md")

    outcomes = cutover.archive_entries(project_root=tmp_path, entries=[entry], project="demo")

    assert (tmp_path / "notes.md").read_text() == _own_pointer(entry)
    assert outcomes[0].pointer_path == "notes.md" and outcomes[0].occupied == ()
    assert _pointer_files(tmp_path) == []


def test_leave_no_pointer_files_plans_nothing_and_leaves_the_md_alone(tmp_path):
    _project(tmp_path, "data/things.csv")
    (tmp_path / "data/things.md").write_text(REAL)

    outcomes = cutover.archive_entries(
        project_root=tmp_path, entries=[_entry("data/things.csv")], project="demo",
        write_pointer_files=False,
    )

    assert outcomes == []
    assert (tmp_path / "data/things.md").read_text() == REAL
    assert _pointer_files(tmp_path) == []


def test_a_failing_pointer_write_still_leaves_the_manifest_line(tmp_path, monkeypatch):
    _project(tmp_path, "data/things.csv")

    def boom(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(cutover, "_write_pointer_file", boom)

    with pytest.raises(OSError):
        cutover.archive_entries(
            project_root=tmp_path, entries=[_entry("data/things.csv")], project="demo")

    assert "data/things.csv" in _log(tmp_path)


def test_a_differently_cased_md_is_never_overwritten(tmp_path):
    _project(tmp_path, "things.csv")
    (tmp_path / "things.MD").write_text(REAL)

    cutover.archive_entries(project_root=tmp_path, entries=[_entry("things.csv")], project="d")

    assert (tmp_path / "things.MD").read_text() == REAL
