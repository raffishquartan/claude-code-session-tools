from __future__ import annotations

import sqlite3

import pytest

from cc_session_tools.lib import db
from cc_session_tools.lib.pdata import formats, repository, store, verify
from .legacy_fixture import make_format3_db, snapshot_dir


@pytest.fixture
def db_dir(monkeypatch, tmp_path):
    d = tmp_path / "project-db"
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(d))
    return d


def test_format_constants():
    assert formats.PDATA_FORMAT_INTEGER_IDS == 3
    assert formats.PDATA_FORMAT_UUID_IDS == 4
    assert formats.CURRENT_PDATA_FORMAT == formats.PDATA_FORMAT_UUID_IDS
    assert formats.UUID_IDS_MIGRATION == "pdata-uuid-ids"


def test_probe_on_a_fresh_database_is_format_4(db_dir):
    conn = repository.connect("fresh")
    try:
        assert formats.pdata_format(conn) == formats.PDATA_FORMAT_UUID_IDS
    finally:
        conn.close()


def test_probe_on_an_empty_file_with_no_records_table_is_current(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    try:
        assert formats.pdata_format(conn) == formats.CURRENT_PDATA_FORMAT
    finally:
        conn.close()


def test_probe_on_a_format_3_database(db_dir):
    make_format3_db(db_dir / "old.db")
    conn = sqlite3.connect(str(db_dir / "old.db"))
    try:
        assert formats.pdata_format(conn) == formats.PDATA_FORMAT_INTEGER_IDS
    finally:
        conn.close()


def test_probe_rejects_an_unknown_id_type(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "odd.db"))
    try:
        conn.execute("CREATE TABLE records (id BLOB PRIMARY KEY)")
        with pytest.raises(formats.UnknownPdataFormatError, match="BLOB"):
            formats.pdata_format(conn)
    finally:
        conn.close()


def test_database_format_of_a_missing_file_is_none(db_dir):
    assert formats.database_format(store.db_path("absent")) is None
    assert not store.db_path("absent").exists()


def test_database_format_reads_without_writing(db_dir):
    make_format3_db(db_dir / "old.db")
    before = snapshot_dir(db_dir)
    assert formats.database_format(db_dir / "old.db") == formats.PDATA_FORMAT_INTEGER_IDS
    assert snapshot_dir(db_dir) == before


def test_new_database_is_created_format_4_with_text_ids_and_marker(db_dir):
    conn = repository.connect("fresh")
    try:
        id_type = {r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(records)")}["id"]
        assert id_type == "TEXT"
        assert db.migration_applied(conn, formats.UUID_IDS_MIGRATION)
        with repository._immediate(conn):
            repository.ensure_extension_table(conn, "notes")
        ext_type = {
            r["name"]: r["type"] for r in conn.execute('PRAGMA table_info("ext_notes")')
        }["record_id"]
        assert ext_type == "TEXT"
        verify.ensure_verify_tables(conn)
        wm_type = {
            r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(pdata_verify_watermark)")
        }["record_id"]
        assert wm_type == "TEXT"
    finally:
        conn.close()


def test_connecting_to_a_format_3_database_leaves_it_format_3_and_unmarked(db_dir):
    make_format3_db(db_dir / "old.db")
    conn = repository.connect("old")
    try:
        assert formats.pdata_format(conn) == formats.PDATA_FORMAT_INTEGER_IDS
        assert not db.migration_applied(conn, formats.UUID_IDS_MIGRATION)
    finally:
        conn.close()


def test_verify_watermark_on_a_format_3_database_keeps_integer_ids(db_dir):
    make_format3_db(db_dir / "old.db")
    conn = repository.connect("old")
    try:
        verify.ensure_verify_tables(conn)
        wm_type = {
            r["name"]: r["type"] for r in conn.execute("PRAGMA table_info(pdata_verify_watermark)")
        }["record_id"]
        assert wm_type == "INTEGER"
    finally:
        conn.close()


def test_projects_needing_migration_lists_only_format_3(db_dir):
    make_format3_db(db_dir / "old.db")
    repository.connect("new").close()
    assert formats.projects_needing_migration(["new", "old", "absent"]) == ["old"]


def test_legacy_format_message_names_the_command_and_runbook():
    message = formats.legacy_format_message("old")
    assert "ccst pdata migrate-uuid --project old" in message
    assert "docs/pdata-uuid-migration-runbook.md" in message
    assert "format 3" in message


def test_exempt_verbs():
    assert {"migrate-uuid", "sync-check", "verify"} <= formats.FORMAT_GATE_EXEMPT_VERBS
    for verb in ("add", "get", "list", "query", "update", "delete", "restore", "schema",
                 "init", "dump", "rehydrate", "resolve", "reorganize", "rename-group",
                 "reconcile-session-output"):
        assert verb not in formats.FORMAT_GATE_EXEMPT_VERBS
