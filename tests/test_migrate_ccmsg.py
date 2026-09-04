# tests/test_migrate_ccmsg.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_session_tools.cli.migrate_ccmsg import migrate
from cc_session_tools.lib import db as db_lib
from cc_session_tools.lib.messaging import message, repository, store


def _old_message_file(old_root: Path, partition: str, mid: str, subject: str) -> None:
    inbox = old_root / partition / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    m = message.Message(
        id=mid, schema=1, from_project="o", from_session="s", from_uuid="u",
        to_kind="project", to_value="alpha", to_location=partition,
        subject=subject, sent_at="2026-06-20T00:00:00Z", status="sent",
        read_at=None, read_by_uuid=None, read_by_session=None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=["/abs/a.md"], body="Body.",
    )
    (inbox / f"{mid}__slug.md").write_text(message.serialise(m), encoding="utf-8")


def test_migrate_moves_messages_and_cursors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(new_root))
    _old_message_file(old_root, "projects/alpha", "20260620T000000Z-0001", "One")
    _old_message_file(old_root, "_global", "20260620T000000Z-0002", "Two")
    cursors = old_root / ".cursors"
    cursors.mkdir(parents=True)
    (cursors / "uuid-1.json").write_text(
        json.dumps({"high_water": {"projects/alpha": "20260620T000000Z-0001"}}),
        encoding="utf-8",
    )
    backups = tmp_path / "backups"

    rc = migrate(old_root=old_root, backup_dir=backups, dry_run=False)
    assert rc == 0

    assert repository.get_by_id("20260620T000000Z-0001").subject == "One"
    assert repository.get_by_id("20260620T000000Z-0002").subject == "Two"
    assert repository.load_cursor("uuid-1") == {"projects/alpha": "20260620T000000Z-0001"}
    assert list(backups.glob("ccmsg-*.tar.gz"))         # backup taken
    assert not (old_root / "projects").exists()          # old tree removed after verify


def test_migrate_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_root = tmp_path / "old"
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path / "new"))
    _old_message_file(old_root, "projects/alpha", "20260620T000000Z-0001", "One")
    assert migrate(old_root=old_root, backup_dir=tmp_path / "b", dry_run=True) == 0
    assert not store.db_path().exists()
    assert (old_root / "projects").exists()              # untouched


def test_migrate_rerun_after_success_does_not_false_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A re-run against an already-migrated (now empty) old_root must not abort
    # just because parsed count (0) is less than the DB's total row count —
    # the verify check is >=, not exact-equality, precisely so a cautious
    # re-run or a re-run after an interrupted delete doesn't false-fail.
    old_root = tmp_path / "old"
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path / "new"))
    _old_message_file(old_root, "projects/alpha", "20260620T000000Z-0001", "One")
    backups = tmp_path / "backups"

    assert migrate(old_root=old_root, backup_dir=backups, dry_run=False) == 0
    assert not (old_root / "projects").exists()  # old tree removed after first run

    # old_root re-created empty (as if the operator re-ran the script by hand
    # after the first run already succeeded and deleted everything).
    old_root.mkdir(parents=True, exist_ok=True)
    rc = migrate(old_root=old_root, backup_dir=backups, dry_run=False)
    assert rc == 0
    assert repository.get_by_id("20260620T000000Z-0001").subject == "One"  # still there


def test_migration_records_completion_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_root = tmp_path / "old"
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path / "new"))
    _old_message_file(old_root, "projects/alpha", "20260620T000000Z-0001", "One")
    migrate(old_root=old_root, backup_dir=tmp_path / "backups", dry_run=False)
    conn = repository.connect()
    try:
        assert db_lib.migration_applied(conn, repository.LEGACY_FLAT_FILE_MIGRATION)
    finally:
        conn.close()


def test_refuses_to_reimport_when_marker_already_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_root = tmp_path / "old"
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path / "new"))
    _old_message_file(old_root, "projects/alpha", "20260620T000000Z-0001", "One")
    backups = tmp_path / "backups"
    migrate(old_root=old_root, backup_dir=backups, dry_run=False)

    # Legacy files reappear (e.g. a manually-restored old_root) after this store is
    # already marked migrated - must refuse, not double-import.
    _old_message_file(old_root, "projects/alpha", "20260620T000000Z-0002", "Two")
    rc = migrate(old_root=old_root, backup_dir=backups, dry_run=False)
    assert rc == 1
    assert repository.get_by_id("20260620T000000Z-0002") is None  # not imported
