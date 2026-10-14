# tests/test_claude_md_drift.py
"""Hand-edit detection, backup and reporting for the managed CLAUDE.md blocks."""
from __future__ import annotations

import subprocess
import sqlite3
import sys
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from cc_session_tools.lib import claude_md_install, claude_md_store, sessions_db
from cc_session_tools.lib.claude_md_install import (
    MarkdownAction,
    find_hand_edited_sections,
    install_claude_md,
    uninstall_claude_md,
)
from cc_session_tools.lib.doctor import Status, check_claude_md_drift


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sessions.db"


@pytest.fixture
def md(tmp_path: Path) -> Path:
    return tmp_path / "CLAUDE.md"


def _hand_edit(md: Path, section: str, marker: str = "HAND EDIT") -> None:
    text = md.read_text()
    end = f"<!-- CCST:{section} END -->"
    md.write_text(text.replace(end, f"{marker}\n{end}", 1))


def test_apply_records_a_hash_per_installed_section(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    recorded = claude_md_store.read_recorded_hashes(md, db_path=db_path)
    assert sorted(recorded) == sorted(claude_md_install.SECTION_IDS)


def test_dry_run_records_nothing(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=False, db_path=db_path)
    assert claude_md_store.read_recorded_hashes(md, db_path=db_path) == {}
    assert not db_path.exists()


def test_uninstall_clears_only_the_removed_sections_record(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    uninstall_claude_md(md, apply=True, sections=["workflow"], db_path=db_path)
    recorded = claude_md_store.read_recorded_hashes(md, db_path=db_path)
    assert "workflow" not in recorded
    assert "messaging" in recorded


def test_hand_edited_block_is_backed_up_then_replaced(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    _hand_edit(md, "workflow")
    edited_text = md.read_text()

    results = install_claude_md(md, apply=True, db_path=db_path)

    replaced = next(r for r in results if r.section == "workflow")
    assert replaced.action is MarkdownAction.REPLACED
    assert replaced.drifted and replaced.backup_id is not None
    assert "HAND EDIT" not in md.read_text()
    backup = claude_md_store.get_backup(replaced.backup_id, db_path=db_path)
    assert backup is not None and backup.section == "workflow"
    assert "HAND EDIT" in backup.text
    assert backup.text in edited_text
    assert all(r.backup_id is None for r in results if r.section != "workflow")


def test_out_of_date_template_is_replaced_without_a_backup(
    md: Path, db_path: Path, mocker: MockerFixture
) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    bodies = dict(claude_md_install._SECTION_BODIES)
    bodies["workflow"] = bodies["workflow"] + "A newer template line.\n"
    mocker.patch.object(claude_md_install, "_SECTION_BODIES", bodies)

    results = install_claude_md(md, apply=True, db_path=db_path)

    replaced = next(r for r in results if r.section == "workflow")
    assert replaced.action is MarkdownAction.REPLACED
    assert not replaced.drifted and replaced.backup_id is None
    assert claude_md_store.list_backups(db_path=db_path) == []
    assert "A newer template line." in md.read_text()


def test_differing_block_with_no_record_is_backed_up(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    db_path.unlink()
    _hand_edit(md, "agents")

    results = install_claude_md(md, apply=True, db_path=db_path)

    agents = next(r for r in results if r.section == "agents")
    assert agents.drifted and agents.backup_id is not None


def test_dry_run_reports_drift_without_writing_a_backup_or_the_file(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    _hand_edit(md, "workflow")
    before = md.read_text()

    results = install_claude_md(md, apply=False, db_path=db_path)

    assert next(r for r in results if r.section == "workflow").drifted
    assert all(r.backup_id is None for r in results)
    assert claude_md_store.list_backups(db_path=db_path) == []
    assert md.read_text() == before


def test_unusable_store_does_not_block_the_install_and_is_reported(
    md: Path, db_path: Path
) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    _hand_edit(md, "workflow")
    db_path.write_bytes(b"not a sqlite database file")

    results = install_claude_md(md, apply=True, db_path=db_path)

    workflow = next(r for r in results if r.section == "workflow")
    assert workflow.drifted and workflow.backup_id is None and workflow.backup_error
    assert "HAND EDIT" not in md.read_text()


def test_find_hand_edited_sections(md: Path, db_path: Path) -> None:
    assert find_hand_edited_sections(md, db_path=db_path) == []
    install_claude_md(md, apply=True, db_path=db_path)
    assert find_hand_edited_sections(md, db_path=db_path) == []
    _hand_edit(md, "agents")
    assert find_hand_edited_sections(md, db_path=db_path) == ["agents"]


def test_doctor_check_ok_when_no_block_is_edited(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    assert check_claude_md_drift(md, db_path).status is Status.OK


def test_doctor_check_warns_naming_the_edited_section(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    _hand_edit(md, "workflow")
    result = check_claude_md_drift(md, db_path)
    assert result.status is Status.WARN
    assert "workflow" in result.reason and "backup" in result.reason


def test_doctor_check_warns_on_malformed_markers(md: Path, db_path: Path) -> None:
    md.write_text("<!-- CCST:workflow START -->\nno end marker\n")
    assert check_claude_md_drift(md, db_path).status is Status.WARN


def test_connect_adds_the_new_tables_to_an_old_schema_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE install_sync (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.commit()
    conn.close()

    conn = sessions_db.connect(path=db_path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"claude_md_blocks", "claude_md_backups"} <= names


def test_get_backup_for_unknown_id_is_none(db_path: Path) -> None:
    assert claude_md_store.get_backup(99, db_path=db_path) is None


def test_cli_install_warns_and_backups_list_show_round_trip(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    assert _run("claude-md", "install", "--target", str(md), "--apply").returncode == 0
    _hand_edit(md, "workflow")

    applied = _run("claude-md", "install", "--target", str(md), "--apply")
    assert applied.returncode == 0
    assert claude_md_install.DRIFT_WARNING_MARKER in applied.stdout
    assert "saved as backup 1" in applied.stdout

    listed = _run("claude-md", "backups", "list")
    assert listed.returncode == 0
    assert listed.stdout.startswith("1\t") and "\tworkflow" in listed.stdout

    shown = _run("claude-md", "backups", "show", "1")
    assert shown.returncode == 0
    assert "HAND EDIT" in shown.stdout and shown.stdout.startswith("<!-- CCST:workflow START -->")


def test_cli_install_dry_run_warns_that_apply_will_overwrite(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    _run("claude-md", "install", "--target", str(md), "--apply")
    _hand_edit(md, "workflow")
    dry = _run("claude-md", "install", "--target", str(md))
    assert claude_md_install.DRIFT_WARNING_MARKER in dry.stdout
    assert "will save its text as a backup" in dry.stdout
    assert "HAND EDIT" in md.read_text()


def test_cli_backups_show_unknown_id_fails_naming_the_id() -> None:
    result = _run("claude-md", "backups", "show", "424242")
    assert result.returncode == 1
    assert "424242" in result.stderr


def test_hash_recording_failure_after_the_write_does_not_crash_or_hide_the_backup(
    md: Path, db_path: Path, mocker: MockerFixture
) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    _hand_edit(md, "workflow")
    mocker.patch.object(
        claude_md_store, "record_hashes", side_effect=sqlite3.OperationalError("database is locked")
    )

    results = install_claude_md(md, apply=True, db_path=db_path)

    workflow = next(r for r in results if r.section == "workflow")
    assert workflow.backup_id is not None and workflow.backup_error is None
    assert "HAND EDIT" not in md.read_text()


def test_unusable_data_directory_does_not_block_install_or_uninstall(
    tmp_path: Path, md: Path
) -> None:
    install_claude_md(md, apply=True, db_path=tmp_path / "sessions.db")
    _hand_edit(md, "workflow")
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory")
    bad_db = blocked / "sessions.db"

    installed = install_claude_md(md, apply=True, sections=["workflow"], db_path=bad_db)
    assert next(r for r in installed if r.section == "workflow").backup_error
    assert "HAND EDIT" not in md.read_text()

    removed = uninstall_claude_md(md, apply=True, sections=["workflow"], db_path=bad_db)
    assert removed[0].action is MarkdownAction.REMOVED
    assert "CCST:workflow" not in md.read_text()


def test_uninstall_backs_up_a_hand_edited_block(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    _hand_edit(md, "workflow")

    results = uninstall_claude_md(md, apply=True, sections=["workflow"], db_path=db_path)

    assert results[0].drifted and results[0].backup_id is not None
    backup = claude_md_store.get_backup(results[0].backup_id, db_path=db_path)
    assert backup is not None and "HAND EDIT" in backup.text
    assert "CCST:workflow" not in md.read_text()


def test_uninstall_of_an_untouched_block_takes_no_backup(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    results = uninstall_claude_md(md, apply=True, sections=["workflow"], db_path=db_path)
    assert not results[0].drifted
    assert claude_md_store.list_backups(db_path=db_path) == []


def test_uninstall_with_nothing_removed_does_not_create_the_store(md: Path, db_path: Path) -> None:
    md.write_text("# no managed blocks\n")
    uninstall_claude_md(md, apply=True, db_path=db_path)
    assert not db_path.exists()


def test_uninstall_survives_a_corrupt_store(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    db_path.write_bytes(b"not a sqlite database file")
    results = uninstall_claude_md(md, apply=True, sections=["workflow"], db_path=db_path)
    assert results[0].action is MarkdownAction.REMOVED
    assert "CCST:workflow" not in md.read_text()


def test_listing_or_reading_backups_from_a_corrupt_store_raises_not_empty(db_path: Path) -> None:
    db_path.write_bytes(b"not a sqlite database file")
    with pytest.raises(sqlite3.DatabaseError):
        claude_md_store.list_backups(db_path=db_path)
    with pytest.raises(sqlite3.DatabaseError):
        claude_md_store.get_backup(1, db_path=db_path)


def test_get_backup_returns_only_the_requested_row(md: Path, db_path: Path) -> None:
    install_claude_md(md, apply=True, db_path=db_path)
    _hand_edit(md, "workflow", "FIRST")
    first = next(r for r in install_claude_md(md, apply=True, db_path=db_path) if r.drifted)
    _hand_edit(md, "agents", "SECOND")
    second = next(r for r in install_claude_md(md, apply=True, db_path=db_path) if r.drifted)
    got = claude_md_store.get_backup(second.backup_id or 0, db_path=db_path)
    assert got is not None and got.section == "agents" and "SECOND" in got.text
    assert first.backup_id != second.backup_id


def test_doctor_check_warns_on_an_unreadable_claude_md(tmp_path: Path, db_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_bytes(b"\xff\xfe not utf-8 \x80")
    assert check_claude_md_drift(md, db_path).status is Status.WARN
    assert check_claude_md_drift(tmp_path, db_path).status is Status.WARN  # a directory


def test_cli_backups_on_a_corrupt_store_reports_the_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_home = tmp_path / "data-home"
    data_home.mkdir()
    (data_home / "sessions.db").write_bytes(b"not a sqlite database file")
    monkeypatch.setenv("CCST_DATA_HOME", str(data_home))
    result = _run("claude-md", "backups", "list")
    assert result.returncode == 1
    assert "ccst repair sessions" in result.stderr


def test_drift_warning_text_when_the_backup_could_not_be_saved() -> None:
    from cc_session_tools.cli.ccst import _claude_md_drift_warning

    result = claude_md_install.MarkdownResult(
        Path("/x/CLAUDE.md"), "workflow", MarkdownAction.REPLACED, "replaced", drifted=True,
        backup_error="database is locked",
    )
    text = _claude_md_drift_warning(result, apply=True)
    assert claude_md_install.DRIFT_WARNING_MARKER in text
    assert "could NOT be saved" in text and "database is locked" in text


def test_cli_uninstall_warns_and_names_the_backup(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    _run("claude-md", "install", "--target", str(md), "--apply")
    _hand_edit(md, "workflow")
    out = _run("claude-md", "uninstall", "--target", str(md), "--section", "workflow", "--apply")
    assert out.returncode == 0
    assert claude_md_install.DRIFT_WARNING_MARKER in out.stdout
    assert "removed, previous text saved as backup 1" in out.stdout
