"""Tests for cc_session_tools.cli.migrate_sessions_db — one-shot migration of
the flat tag cache, activity sentinels, and cc-doctor-mutes.json into
sessions.db."""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from cc_session_tools.cli.migrate_sessions_db import run_migration
from cc_session_tools.lib import doctor_mutes, sessions_db


@pytest.fixture
def layout(tmp_path):
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
    }


def _make_session_dir(root: Path, project: str, basename: str) -> Path:
    sess = root / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    return sess


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
    sess = _make_session_dir(layout["root"], "myproj", "20260713-my-feature")
    (sess / ".last-opened").touch()
    (sess / ".last-active").touch()
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    rows = sessions_db.list_sessions(path=layout["db_path"])
    assert len(rows) == 1
    assert rows[0].basename == "20260713-my-feature"
    assert rows[0].last_opened > 0.0
    assert rows[0].last_active > 0.0


def test_migrates_session_with_no_sentinels(layout):
    """A session dir with no .last-opened/.last-active still gets a row (start_date only)."""
    _make_session_dir(layout["root"], "myproj", "20260713-no-sentinels")
    rc = run_migration(
        dry_run=False, db_path=layout["db_path"], tags_dir=layout["tags_dir"],
        mutes_file=layout["mutes_file"], roots=[layout["root"]], backup_dir=layout["backup_dir"],
    )
    assert rc == 0
    rows = sessions_db.list_sessions(path=layout["db_path"])
    assert rows[0].last_opened == 0.0
    assert rows[0].last_active == 0.0


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
    sess = _make_session_dir(layout["root"], "myproj", "20260713-twice")
    (sess / ".last-opened").touch()
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
