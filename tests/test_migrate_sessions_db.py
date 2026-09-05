"""Tests for cc_session_tools.cli.migrate_sessions_db — one-shot migration of
the flat tag cache, activity sentinels, and cc-doctor-mutes.json into
sessions.db."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from cc_session_tools.cli.migrate_sessions_db import (
    _check_sessions_db_not_locked,
    migrate_uuid,
    run_migration,
)
from cc_session_tools.lib import db as db_lib
from cc_session_tools.lib import doctor_mutes, sessions_db


@pytest.fixture
def layout(tmp_path, monkeypatch):
    # HOME is redirected so transcript_dir_for_project() (used to resolve each migrated
    # session's uuid, see resolve_all_uuids_for_basename) resolves under tmp_path instead
    # of the real ~/.claude/projects/.
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    mutes_file = tmp_path / "cc-doctor-mutes.json"
    root = tmp_path / "repos"
    root.mkdir()
    db_path = tmp_path / "sessions.db"
    backup_dir = tmp_path / "backups"
    return {
        "tags_dir": tags_dir,
        "mutes_file": mutes_file,
        "root": root,
        "db_path": db_path,
        "backup_dir": backup_dir,
        "claude_projects": home / ".claude" / "projects",
    }


def _make_session_dir(root: Path, project: str, basename: str) -> Path:
    sess = root / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    return sess


def _write_transcript(claude_projects: Path, project_dir: Path, uuid: str, basename: str) -> Path:
    """Write a minimal JSONL transcript whose custom-title record resolves to `basename` -
    the signal resolve_all_uuids_for_basename() looks for. Matches test_ccr_orphans.py's
    _write_jsonl helper."""
    from cc_session_tools.lib.sessions import transcript_dir_for_project

    transcript_dir = transcript_dir_for_project(project_dir)
    transcript_dir.mkdir(parents=True, exist_ok=True)
    jsonl = transcript_dir / f"{uuid}.jsonl"
    record = {"type": "custom-title", "customTitle": basename, "sessionId": uuid}
    jsonl.write_text(json.dumps(record) + "\n")
    return jsonl


def test_dry_run_writes_nothing(layout):
    (layout["tags_dir"] / "uuid-1.tag").write_text("my-feature\n")
    rc = run_migration(
        dry_run=True, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    assert not layout["db_path"].exists()
    assert not layout["backup_dir"].exists()


def test_migrates_tags(layout):
    (layout["tags_dir"] / "uuid-1.tag").write_text("my-feature\n")
    (layout["tags_dir"] / "uuid-2.tag").write_text("other-feature\n")
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    result = sessions_db.lookup_tags(["uuid-1", "uuid-2"], path=layout["db_path"])
    assert result == {"uuid-1": "my-feature", "uuid-2": "other-feature"}


def test_migrates_activity_sentinels(layout):
    project_dir = layout["root"] / "myproj"
    sess = _make_session_dir(layout["root"], "myproj", "20260713-my-feature")
    (sess / ".last-opened").touch()
    (sess / ".last-active").touch()
    _write_transcript(layout["claude_projects"], project_dir, "uuid-my-feature", "20260713-my-feature")
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    rows = sessions_db.list_sessions(path=layout["db_path"])
    assert len(rows) == 1
    assert rows[0].basename == "20260713-my-feature"
    assert rows[0].uuid == "uuid-my-feature"
    assert rows[0].last_opened > 0.0
    assert rows[0].last_active > 0.0


def test_migrates_session_with_no_sentinels(layout):
    """A session dir with no .last-opened/.last-active still gets a row (start_date only)."""
    project_dir = layout["root"] / "myproj"
    _make_session_dir(layout["root"], "myproj", "20260713-no-sentinels")
    _write_transcript(layout["claude_projects"], project_dir, "uuid-no-sentinels", "20260713-no-sentinels")
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    rows = sessions_db.list_sessions(path=layout["db_path"])
    assert rows[0].last_opened == 0.0
    assert rows[0].last_active == 0.0


def test_migrates_session_with_no_matching_transcript_is_flagged_ambiguous(layout):
    """No transcript resolves this basename's uuid: the row must land in the
    migration-ambiguous sidecar, never guessed into `sessions` (design.md Decision 7)."""
    _make_session_dir(layout["root"], "myproj", "20260713-no-transcript")
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    assert sessions_db.list_sessions(path=layout["db_path"]) == []
    ambiguous = sessions_db.list_ambiguous_rows(path=layout["db_path"])
    assert len(ambiguous) == 1
    assert ambiguous[0].basename == "20260713-no-transcript"
    assert ambiguous[0].reason == "no-transcript-found"


def test_migrates_forked_session_into_two_rows(layout):
    """Two transcripts resolve to the same basename (a genuine fork, not ambiguity):
    both uuids get their own row (design.md Decision 6)."""
    project_dir = layout["root"] / "myproj"
    sess = _make_session_dir(layout["root"], "myproj", "20260713-forked")
    (sess / ".last-opened").touch()
    _write_transcript(layout["claude_projects"], project_dir, "uuid-fork-a", "20260713-forked")
    _write_transcript(layout["claude_projects"], project_dir, "uuid-fork-b", "20260713-forked")
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    rows = sessions_db.list_sessions(path=layout["db_path"])
    assert {r.uuid for r in rows} == {"uuid-fork-a", "uuid-fork-b"}
    assert sessions_db.list_ambiguous_rows(path=layout["db_path"]) == []


def test_migrates_doctor_mutes(layout):
    layout["mutes_file"].write_text(json.dumps({"version:pypi": "2026-07-01"}))
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    assert doctor_mutes.load_mutes(layout["db_path"]) == {"version:pypi": "2026-07-01"}


def test_writes_tar_backup_of_old_sources(layout):
    (layout["tags_dir"] / "uuid-1.tag").write_text("my-feature\n")
    layout["mutes_file"].write_text(json.dumps({"a": "2026-07-01"}))
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    backups = list(layout["backup_dir"].glob("*.tar.gz"))
    assert len(backups) == 1
    with tarfile.open(backups[0]) as tf:
        names = tf.getnames()
    assert "tags" in names or any("uuid-1.tag" in n for n in names)


def test_does_not_delete_old_sources(layout):
    tag_file = layout["tags_dir"] / "uuid-1.tag"
    tag_file.write_text("my-feature\n")
    layout["mutes_file"].write_text(json.dumps({"a": "2026-07-01"}))
    run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert tag_file.exists()
    assert layout["mutes_file"].exists()


def test_missing_sources_are_a_no_op_not_an_error(layout):
    """No tags dir, no mutes file, no roots with sessions — migration succeeds
    with zero rows written."""
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    assert sessions_db.list_sessions(path=layout["db_path"]) == []


def test_run_twice_is_idempotent(layout):
    (layout["tags_dir"] / "uuid-1.tag").write_text("my-feature\n")
    project_dir = layout["root"] / "myproj"
    sess = _make_session_dir(layout["root"], "myproj", "20260713-twice")
    (sess / ".last-opened").touch()
    _write_transcript(layout["claude_projects"], project_dir, "uuid-twice", "20260713-twice")
    run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    rows = sessions_db.list_sessions(path=layout["db_path"])
    assert len(rows) == 1  # not duplicated


def test_migration_records_completion_marker(layout):
    (layout["tags_dir"] / "uuid-1.tag").write_text("my-feature\n")
    run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    conn = sessions_db.connect(path=layout["db_path"])
    try:
        assert db_lib.migration_applied(conn, sessions_db.LEGACY_FLAT_FILE_MIGRATION)
    finally:
        conn.close()


def test_dry_run_does_not_record_marker(layout):
    (layout["tags_dir"] / "uuid-1.tag").write_text("my-feature\n")
    run_migration(
        dry_run=True, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert not layout["db_path"].exists()


# ---------------------------------------------------------------------------
# migrate_uuid — the 3.0.0 schema-rebuild migration (design.md Decision 3-4)
# ---------------------------------------------------------------------------

def _seed_old_schema_db(db_path: Path, rows: list[dict]) -> None:
    """Build a realistic pre-3.0.0 sessions.db: 2-column PK, no uuid column, with the
    `updated_at` column the live schema already has (the original spec's own DDL omitted
    it - design.md Decision 4 - this fixture deliberately does not repeat that mistake)."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE sessions (project_dir TEXT NOT NULL, basename TEXT NOT NULL, "
        "start_date TEXT NOT NULL, last_opened REAL, last_active REAL, "
        "discovered_at TEXT NOT NULL, updated_at TEXT, "
        "PRIMARY KEY (project_dir, basename))"
    )
    for r in rows:
        conn.execute(
            "INSERT INTO sessions (project_dir, basename, start_date, last_opened, "
            "last_active, discovered_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                r["project_dir"], r["basename"], r["start_date"], r.get("last_opened"),
                r.get("last_active"), r["discovered_at"], r.get("updated_at"),
            ),
        )
    conn.commit()
    # Switch to WAL now, while nothing else holds the file: db.connect() (used throughout
    # migrate_uuid) always re-applies PRAGMA journal_mode=WAL, which - unlike ordinary
    # statements - ignores busy_timeout and fails immediately against a locked non-WAL
    # file (lib/db.py's _enable_wal docstring). Priming WAL here means a later BEGIN
    # IMMEDIATE lock (used to test the concurrency guard) blocks only the guard's own
    # probe, not this pragma.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()


def test_migrate_uuid_on_nonexistent_db_is_a_no_op(tmp_path):
    db_path = tmp_path / "sessions.db"
    assert migrate_uuid(db_path=db_path, dry_run=False) == 0
    assert not db_path.exists()


def test_migrate_uuid_dry_run_reports_without_writing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    db_path = tmp_path / "sessions.db"
    _seed_old_schema_db(db_path, [
        {"project_dir": "/repos/myproj", "basename": "20260101-good",
         "start_date": "20260101", "discovered_at": "2026-01-01T00:00:00Z",
         "updated_at": "2026-01-01T00:00:00Z", "last_active": 500.0},
    ])
    _write_transcript(home / ".claude" / "projects", Path("/repos/myproj"), "uuid-good", "20260101-good")

    rc = migrate_uuid(db_path=db_path, dry_run=True)

    assert rc == 0
    conn = sqlite3_connect_readonly(db_path)
    pk_cols = {row["name"]: row["pk"] for row in conn.execute("PRAGMA table_info(sessions)")}
    conn.close()
    assert pk_cols.get("uuid") is None  # dry-run must not touch the schema


def sqlite3_connect_readonly(db_path: Path):
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def test_migrate_uuid_write_rebuilds_schema_and_preserves_updated_at(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    db_path = tmp_path / "sessions.db"
    _seed_old_schema_db(db_path, [
        {"project_dir": "/repos/myproj", "basename": "20260101-good",
         "start_date": "20260101", "discovered_at": "2026-01-01T00:00:00Z",
         "updated_at": "2026-01-01T12:34:56Z", "last_active": 500.0},
    ])
    _write_transcript(home / ".claude" / "projects", Path("/repos/myproj"), "uuid-good", "20260101-good")
    backup_dir = tmp_path / "backups"

    rc = migrate_uuid(db_path=db_path, dry_run=False, backup_dir=backup_dir)

    assert rc == 0
    conn = sessions_db.connect(path=db_path)
    try:
        assert sessions_db.sessions_schema_is_uuid_keyed(conn)
        assert db_lib.migration_applied(conn, sessions_db.SESSIONS_UUID_MIGRATION)
    finally:
        conn.close()
    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1
    assert rows[0].uuid == "uuid-good"
    assert rows[0].last_active == 500.0
    conn = sqlite3_connect_readonly(db_path)
    updated_at = conn.execute(
        "SELECT updated_at FROM sessions WHERE basename='20260101-good'"
    ).fetchone()["updated_at"]
    conn.close()
    assert updated_at == "2026-01-01T12:34:56Z"
    assert len(list(backup_dir.glob("sessions-pre-3.0.0-*.db"))) == 1


def test_migrate_uuid_ambiguous_row_flagged_not_written(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    db_path = tmp_path / "sessions.db"
    _seed_old_schema_db(db_path, [
        {"project_dir": "/repos/myproj", "basename": "20260101-no-transcript",
         "start_date": "20260101", "discovered_at": "2026-01-01T00:00:00Z"},
    ])

    rc = migrate_uuid(db_path=db_path, dry_run=False, backup_dir=tmp_path / "backups")

    assert rc == 0
    assert sessions_db.list_sessions(path=db_path) == []
    ambiguous = sessions_db.list_ambiguous_rows(path=db_path)
    assert len(ambiguous) == 1
    assert ambiguous[0].basename == "20260101-no-transcript"


def test_migrate_uuid_fork_produces_two_rows(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    db_path = tmp_path / "sessions.db"
    _seed_old_schema_db(db_path, [
        {"project_dir": "/repos/myproj", "basename": "20260101-forked",
         "start_date": "20260101", "discovered_at": "2026-01-01T00:00:00Z"},
    ])
    _write_transcript(home / ".claude" / "projects", Path("/repos/myproj"), "uuid-a", "20260101-forked")
    _write_transcript(home / ".claude" / "projects", Path("/repos/myproj"), "uuid-b", "20260101-forked")

    rc = migrate_uuid(db_path=db_path, dry_run=False, backup_dir=tmp_path / "backups")

    assert rc == 0
    rows = sessions_db.list_sessions(path=db_path)
    assert {r.uuid for r in rows} == {"uuid-a", "uuid-b"}


def test_migrate_uuid_is_idempotent_on_rerun(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    db_path = tmp_path / "sessions.db"
    _seed_old_schema_db(db_path, [
        {"project_dir": "/repos/myproj", "basename": "20260101-good",
         "start_date": "20260101", "discovered_at": "2026-01-01T00:00:00Z"},
    ])
    _write_transcript(home / ".claude" / "projects", Path("/repos/myproj"), "uuid-good", "20260101-good")
    backup_dir = tmp_path / "backups"

    assert migrate_uuid(db_path=db_path, dry_run=False, backup_dir=backup_dir) == 0
    assert migrate_uuid(db_path=db_path, dry_run=False, backup_dir=backup_dir) == 0

    rows = sessions_db.list_sessions(path=db_path)
    assert len(rows) == 1  # not duplicated
    # Second run recognized the marker and made no second backup.
    assert len(list(backup_dir.glob("sessions-pre-3.0.0-*.db"))) == 1


def test_migrate_uuid_refuses_when_db_is_locked(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    db_path = tmp_path / "sessions.db"
    _seed_old_schema_db(db_path, [
        {"project_dir": "/repos/myproj", "basename": "20260101-good",
         "start_date": "20260101", "discovered_at": "2026-01-01T00:00:00Z"},
    ])

    import sqlite3
    locker = sqlite3.connect(str(db_path))
    locker.execute("BEGIN IMMEDIATE")
    locker.execute("INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
                    "VALUES ('/x', '20260101-lockrow', '20260101', '2026-01-01T00:00:00Z')")
    try:
        with pytest.raises(ValueError, match="another process appears to be using"):
            migrate_uuid(db_path=db_path, dry_run=False, backup_dir=tmp_path / "backups")
    finally:
        locker.rollback()
        locker.close()

    # Refusal must happen before any destructive change - original schema intact.
    conn = sqlite3_connect_readonly(db_path)
    pk_cols = {row["name"]: row["pk"] for row in conn.execute("PRAGMA table_info(sessions)")}
    conn.close()
    assert pk_cols.get("uuid") is None


def test_check_sessions_db_not_locked_noop_when_file_absent(tmp_path):
    _check_sessions_db_not_locked(tmp_path / "nope.db")  # must not raise


def test_check_sessions_db_not_locked_succeeds_when_unlocked(tmp_path):
    db_path = tmp_path / "sessions.db"
    _seed_old_schema_db(db_path, [])
    _check_sessions_db_not_locked(db_path)  # must not raise
