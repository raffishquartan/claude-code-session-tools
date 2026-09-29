"""Upgrade-path tests for a `sessions.db` that predates 3.0.0 and still has legacy flat-file
sources: the 3.0.0 uuid schema rebuild (`migrate_uuid`) and the 1.0.0 legacy import
(`run_migration`), and how `ccst migrate all` / `ccst doctor` order and describe them.

READ THIS BEFORE "FIXING" A FAILURE HERE. The two `_SCHEMA_*` constants below are literal
copies of the `sessions.db` DDL two historical releases shipped (git tags v1.0.0 and v2.14.1),
deliberately NOT derived from `sessions_db.DDL`. They describe databases that already exist on
users' machines, so they must not change. If a test in the "baseline contract" section fails, the
*current* schema or migration code changed in a way that no longer upgrades one of those
databases: update the migration code, and review the tests of every later migration step
(`test_migrate_sessions_db.py`, the doctor tests) in the same change - do not edit the baselines
to make them pass.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pytest

from cc_session_tools.cli import ccst as ccst_mod
from cc_session_tools.cli.migrate_sessions_db import migrate_uuid, run_migration
from cc_session_tools.lib import db as db_lib
from cc_session_tools.lib import doctor, sessions_db
from cc_session_tools.lib.sessions import transcript_dir_for_project

# `git show v1.0.0:src/cc_session_tools/lib/sessions_db.py` (DDL only). No `updated_at` column
# on `sessions`, no `created_at` columns, no `migrations` table.
_SCHEMA_V1_0_0_TO_V2_12_X = """
CREATE TABLE session_tags (
    uuid       TEXT PRIMARY KEY,
    tag        TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE sessions (
    project_dir   TEXT NOT NULL,
    basename      TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    last_opened   REAL,
    last_active   REAL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (project_dir, basename)
);
CREATE TABLE doctor_mutes (
    name     TEXT PRIMARY KEY,
    muted_at TEXT NOT NULL
);
"""

# `git show v2.14.1:src/cc_session_tools/lib/sessions_db.py` (DDL only). `sessions.updated_at`,
# `created_at` columns and the `migrations` table exist; the key is still (project_dir, basename).
_SCHEMA_V2_13_TO_V2_14_X = """
CREATE TABLE session_tags (
    uuid       TEXT PRIMARY KEY,
    tag        TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE sessions (
    project_dir   TEXT NOT NULL,
    basename      TEXT NOT NULL,
    start_date    TEXT NOT NULL,
    last_opened   REAL,
    last_active   REAL,
    discovered_at TEXT NOT NULL,
    updated_at    TEXT,
    PRIMARY KEY (project_dir, basename)
);
CREATE TABLE doctor_mutes (
    name       TEXT PRIMARY KEY,
    muted_at   TEXT NOT NULL,
    created_at TEXT
);
CREATE TABLE migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

SCHEMAS = {
    "v1.0.0-to-v2.12.x": _SCHEMA_V1_0_0_TO_V2_12_X,
    "v2.13-to-v2.14.x": _SCHEMA_V2_13_TO_V2_14_X,
}

BASENAME = "20260101-foo"
UUID_A = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Sandboxed HOME/roots so transcript lookup and root discovery never touch real data."""
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / "repos"
    (root / "p1" / "cc-sessions" / BASENAME).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(root))
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    (tags_dir / f"{UUID_A}.tag").write_text("my-tag")
    mutes = tmp_path / "mutes.json"
    mutes.write_text(json.dumps({"some-check": "2026-01-01"}))
    return {
        "db_path": tmp_path / "data" / "sessions.db",
        "project_dir": root / "p1",
        "tags_dir": tags_dir,
        "mutes_file": mutes,
        "roots": [root],
        "backup_dir": tmp_path / "backups",
    }


def _seed(db_path: Path, schema: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(schema)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()


def _write_transcript(project_dir: Path, uuid: str) -> None:
    d = transcript_dir_for_project(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    record = {"type": "custom-title", "customTitle": BASENAME, "sessionId": uuid}
    (d / f"{uuid}.jsonl").write_text(json.dumps(record) + "\n")


def _snapshot(db_path: Path) -> dict:
    """Everything the 'changes nothing' requirement covers: tables, columns, row counts."""
    conn = sqlite3.connect(str(db_path))
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {
            "master": conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall(),
            "counts": {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables},
            "sessions_cols": conn.execute("PRAGMA table_info(sessions)").fetchall()
            if "sessions" in tables else None,
        }
    finally:
        conn.close()


def _import(env, *, dry_run=False) -> int:
    return run_migration(
        dry_run=dry_run, db_path=env["db_path"], tags_dir=env["tags_dir"],
        mutes_file=env["mutes_file"], roots=env["roots"], backup_dir=env["backup_dir"],
    )


# ---------------------------------------------------------------------------
# Baseline contract: each shipped pre-3.0.0 shape upgrades. If these fail, the schema or the
# migrations changed - see the module docstring before touching the baselines.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("shape", SCHEMAS)
@pytest.mark.parametrize("with_transcript", [True, False], ids=["transcript", "no-transcript"])
def test_baseline_contract_shipped_schema_upgrades_via_rebuild_then_import(
    env, shape, with_transcript, capsys
):
    _seed(env["db_path"], SCHEMAS[shape])
    if with_transcript:
        _write_transcript(env["project_dir"], UUID_A)

    assert migrate_uuid(db_path=env["db_path"], dry_run=False) == 0
    assert _import(env) == 0

    conn = db_lib.connect(env["db_path"], readonly=True)
    try:
        assert sessions_db.sessions_schema_is_uuid_keyed(conn)
        assert db_lib.migration_applied(conn, sessions_db.SESSIONS_UUID_MIGRATION)
        assert db_lib.migration_applied(conn, sessions_db.LEGACY_FLAT_FILE_MIGRATION)
    finally:
        conn.close()
    assert sessions_db.lookup_tags([UUID_A], path=env["db_path"]) == {UUID_A: "my-tag"}
    assert list(_mutes(env["db_path"])) == ["some-check"]


def _mutes(db_path: Path) -> dict[str, str]:
    from cc_session_tools.lib import doctor_mutes
    return doctor_mutes.load_mutes(db_path)


@pytest.mark.parametrize("shape", SCHEMAS)
def test_baseline_contract_shipped_schema_is_detected_as_pre_uuid(env, shape):
    _seed(env["db_path"], SCHEMAS[shape])
    assert sessions_db.pre_uuid_sessions_table(env["db_path"]) is True


@pytest.mark.parametrize("shape", SCHEMAS)
def test_baseline_contract_shipped_schema_dry_run_reads_legacy_rows(env, shape, capsys):
    """Both shapes' `sessions` rows must be readable by the rebuild's plan step (the
    v1.0.0 shape has no `updated_at`)."""
    _seed(env["db_path"], SCHEMAS[shape])
    conn = sqlite3.connect(str(env["db_path"]))
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES (?, ?, '2026-01-01', '2026-01-01T00:00:00Z')",
        (str(env["project_dir"]), BASENAME),
    )
    conn.commit()
    conn.close()
    _write_transcript(env["project_dir"], UUID_A)

    assert migrate_uuid(db_path=env["db_path"], dry_run=True) == 0
    assert "Legacy rows examined: 1" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The reported bug: import first
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("shape", SCHEMAS)
@pytest.mark.parametrize("with_transcript", [True, False], ids=["transcript", "no-transcript"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["write", "dry-run"])
def test_standalone_import_refuses_pre_uuid_db_and_changes_nothing(
    env, shape, with_transcript, dry_run, capsys
):
    _seed(env["db_path"], SCHEMAS[shape])
    if with_transcript:
        _write_transcript(env["project_dir"], UUID_A)
    before = _snapshot(env["db_path"])

    rc = _import(env, dry_run=dry_run)

    assert rc != 0
    assert _snapshot(env["db_path"]) == before
    assert not env["backup_dir"].exists()
    err = capsys.readouterr()
    assert "ccst sessions migrate-uuid --write" in err.out + err.err
    assert "plain terminal" in err.out + err.err


def test_standalone_import_on_corrupt_db_exits_cleanly(env, capsys):
    env["db_path"].parent.mkdir(parents=True)
    env["db_path"].write_bytes(b"this is not a sqlite database" * 50)

    assert _import(env) != 0
    assert "sessions.db" in capsys.readouterr().err


def test_import_on_absent_db_creates_current_schema(env):
    assert not env["db_path"].exists()
    assert _import(env) == 0
    conn = db_lib.connect(env["db_path"], readonly=True)
    try:
        assert sessions_db.sessions_schema_is_uuid_keyed(conn)
    finally:
        conn.close()


def test_import_on_db_without_sessions_table_is_not_refused(env):
    _seed(env["db_path"], "CREATE TABLE unrelated (x TEXT);")
    assert sessions_db.pre_uuid_sessions_table(env["db_path"]) is False
    assert _import(env) == 0


# ---------------------------------------------------------------------------
# migrate_uuid starting-structure handling
# ---------------------------------------------------------------------------

def test_migrate_uuid_names_missing_required_column_and_changes_nothing(env, tmp_path, capsys):
    _seed(env["db_path"], _SCHEMA_V1_0_0_TO_V2_12_X.replace("discovered_at TEXT NOT NULL,", ""))
    before = _snapshot(env["db_path"])

    rc = migrate_uuid(db_path=env["db_path"], dry_run=False, backup_dir=tmp_path / "bk")

    assert rc != 0
    assert "discovered_at" in capsys.readouterr().err
    assert _snapshot(env["db_path"]) == before
    assert not (tmp_path / "bk").exists()


def test_migrate_uuid_without_sessions_table_creates_schema_and_marks(env):
    _seed(env["db_path"], "CREATE TABLE unrelated (x TEXT);")
    assert migrate_uuid(db_path=env["db_path"], dry_run=False) == 0
    conn = db_lib.connect(env["db_path"], readonly=True)
    try:
        assert sessions_db.sessions_schema_is_uuid_keyed(conn)
        assert db_lib.migration_applied(conn, sessions_db.SESSIONS_UUID_MIGRATION)
    finally:
        conn.close()


def test_migrate_uuid_uuid_keyed_without_marker_records_marker_only(env, tmp_path):
    """A forked, already-uuid-keyed table with no marker used to crash with a UNIQUE error
    after writing a backup."""
    _seed(env["db_path"], sessions_db.DDL)
    conn = sqlite3.connect(str(env["db_path"]))
    for u in (UUID_A, "22222222-2222-2222-2222-222222222222"):
        conn.execute(
            "INSERT INTO sessions (project_dir, basename, uuid, start_date, discovered_at) "
            "VALUES (?, ?, ?, '2026-01-01', '2026-01-01T00:00:00Z')",
            (str(env["project_dir"]), BASENAME, u),
        )
    conn.commit()
    conn.close()
    _write_transcript(env["project_dir"], UUID_A)

    assert migrate_uuid(db_path=env["db_path"], dry_run=False, backup_dir=tmp_path / "bk") == 0

    conn = db_lib.connect(env["db_path"], readonly=True)
    try:
        assert db_lib.migration_applied(conn, sessions_db.SESSIONS_UUID_MIGRATION)
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ccst migrate all
# ---------------------------------------------------------------------------

@pytest.fixture
def migrate_all_env(env, monkeypatch):
    """Point the sessions step at the sandbox and stub the other three steps: their default
    paths are import-time Path.home() values and their real runs delete files."""
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(env["db_path"].parent))
    monkeypatch.setattr(
        "cc_session_tools.cli.migrate_sessions_db.DEFAULT_TAGS_DIR", env["tags_dir"])
    monkeypatch.setattr(
        "cc_session_tools.cli.migrate_sessions_db.DEFAULT_MUTES_FILE", env["mutes_file"])
    calls: list[str] = []
    for name in ("ccmsg", "ccsched", "telemetry"):
        monkeypatch.setattr(
            ccst_mod, f"_cmd_migrate_{name}", lambda a, n=name: calls.append(n) or 0)
    env["other_calls"] = calls
    return env


def _migrate_all(dry_run: bool) -> int:
    return ccst_mod._cmd_migrate_all(argparse.Namespace(dry_run=dry_run))


@pytest.mark.parametrize("shape", SCHEMAS)
def test_migrate_all_rebuilds_then_imports_pre_uuid_db(migrate_all_env, shape, monkeypatch):
    env = migrate_all_env
    _seed(env["db_path"], SCHEMAS[shape])
    _write_transcript(env["project_dir"], UUID_A)
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(env["roots"][0]))

    assert _migrate_all(dry_run=False) == 0

    conn = db_lib.connect(env["db_path"], readonly=True)
    try:
        assert sessions_db.sessions_schema_is_uuid_keyed(conn)
        assert db_lib.migration_applied(conn, sessions_db.SESSIONS_UUID_MIGRATION)
        assert db_lib.migration_applied(conn, sessions_db.LEGACY_FLAT_FILE_MIGRATION)
    finally:
        conn.close()
    assert env["other_calls"] == ["ccmsg", "ccsched", "telemetry"]


def test_migrate_all_dry_run_writes_nothing_and_says_import_is_skipped(migrate_all_env, capsys):
    env = migrate_all_env
    _seed(env["db_path"], _SCHEMA_V1_0_0_TO_V2_12_X)
    before = _snapshot(env["db_path"])

    assert _migrate_all(dry_run=True) == 0

    assert _snapshot(env["db_path"]) == before
    assert "skipped until the rebuild has run" in capsys.readouterr().out


def test_migrate_all_refused_rebuild_skips_import_but_runs_other_steps(
    migrate_all_env, monkeypatch, capsys
):
    env = migrate_all_env
    _seed(env["db_path"], _SCHEMA_V1_0_0_TO_V2_12_X)
    before = _snapshot(env["db_path"])

    def locked(_path):
        raise ValueError("another process appears to be using sessions.db")

    monkeypatch.setattr(
        "cc_session_tools.cli.migrate_sessions_db._check_sessions_db_not_locked", locked)

    assert _migrate_all(dry_run=False) != 0

    assert _snapshot(env["db_path"]) == before
    assert env["other_calls"] == ["ccmsg", "ccsched", "telemetry"]
    out = capsys.readouterr()
    assert "sessions" in out.out.split("Steps that failed:")[-1]
    assert "re-run without --dry-run" not in out.out


# ---------------------------------------------------------------------------
# ccst doctor wording
# ---------------------------------------------------------------------------

def _legacy_paths(tmp_path: Path, env) -> doctor.LegacyMigrationPaths:
    return doctor.LegacyMigrationPaths(
        ccmsg_old_root=tmp_path / "no-ccmsg", ccsched_old_dir=tmp_path / "no-ccsched",
        tags_dir=env["tags_dir"], mutes_file=env["mutes_file"],
        telemetry_old_dir=tmp_path / "no-telemetry", data_home=tmp_path / "unused-data-home",
    )


def test_doctor_legacy_import_fail_notes_rebuild_when_schema_is_pre_uuid(env, tmp_path):
    _seed(env["db_path"], _SCHEMA_V1_0_0_TO_V2_12_X)
    results = {
        r.name: r for r in doctor.check_pending_data_store_migration(
            _legacy_paths(tmp_path, env), sessions_db_path=env["db_path"])
    }
    r = results["migration-to-1.0.0:sessions"]
    assert r.status == doctor.Status.FAIL
    assert "ccst migrate all" in r.reason
    assert "rebuild" in r.reason


def test_doctor_legacy_import_fail_unchanged_when_schema_is_current(env, tmp_path):
    _seed(env["db_path"], sessions_db.DDL)
    r = {
        x.name: x for x in doctor.check_pending_data_store_migration(
            _legacy_paths(tmp_path, env), sessions_db_path=env["db_path"])
    }["migration-to-1.0.0:sessions"]
    assert r.status == doctor.Status.FAIL
    assert "rebuild" not in r.reason


@pytest.mark.parametrize("marker", [False, True], ids=["never-migrated", "marker-but-reverted"])
def test_doctor_uuid_fail_mentions_pending_import_in_both_fail_branches(env, marker):
    _seed(env["db_path"], _SCHEMA_V2_13_TO_V2_14_X)
    if marker:
        conn = sqlite3.connect(str(env["db_path"]))
        conn.execute(
            "INSERT INTO migrations (name, applied_at) VALUES (?, 'x')",
            (sessions_db.SESSIONS_UUID_MIGRATION,),
        )
        conn.commit()
        conn.close()
    [r] = doctor.check_sessions_uuid_migration(env["db_path"], legacy_sessions_pending=True)
    assert r.status == doctor.Status.FAIL
    assert "legacy import is also pending" in r.reason
    assert "ccst migrate all" in r.reason

    [plain] = doctor.check_sessions_uuid_migration(env["db_path"], legacy_sessions_pending=False)
    assert "legacy import is also pending" not in plain.reason


def test_doctor_run_all_checks_reads_one_sessions_db_for_both_findings(env, tmp_path):
    """Both findings must come from the same file: the 1.0.0 check used to derive its own
    path from CCST_DATA_HOME while the 3.0.0 check used CCST_SESSIONS_DIR."""
    _seed(env["db_path"], _SCHEMA_V1_0_0_TO_V2_12_X)
    results = {
        r.name: r for r in doctor.run_all_checks(
            installed_version="1.4.1",
            settings_path=tmp_path / "settings.json",
            bundle_path=Path(__file__).parent.parent / "src" / "cc_session_tools" / "config"
            / "hooks-bundle.json",
            skills_source_dir=None,
            skills_target_dir=tmp_path / "skills",
            env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
            skip_pypi=True,
            legacy_migration_paths=_legacy_paths(tmp_path, env),
            sessions_db_path=env["db_path"],
        )
    }
    assert "rebuild" in results["migration-to-1.0.0:sessions"].reason
    assert "legacy import is also pending" in results["migration-to-3.0.0:sessions-uuid"].reason
