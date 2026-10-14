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
