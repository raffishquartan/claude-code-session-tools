"""Tests for `ccst sessions migrate` and `ccst sessions list`."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent.parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path, monkeypatch):
    import os
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(tmp_path / "repos")
    (tmp_path / "repos").mkdir()
    env["CCST_SESSIONS_DIR"] = str(tmp_path / "db")
    return env


def test_sessions_list_empty_db(base_env):
    r = _run(base_env, "sessions", "list")
    assert r.returncode == 0
    assert "No sessions recorded" in r.stdout


def test_sessions_migrate_dry_run_no_sources(base_env):
    r = _run(base_env, "sessions", "migrate", "--dry-run")
    assert r.returncode == 0
    assert "dry-run mode" in r.stdout


def _write_transcript(home: Path, project_dir: Path, uuid: str, basename: str) -> None:
    """Write a minimal JSONL transcript whose custom-title record equals `basename` -
    the signal the sessions-uuid migration's backfill (resolve_all_uuids_for_basename)
    looks for. Without this, a basename with no matching transcript legitimately lands
    in the migration-ambiguous sidecar rather than `sessions` (see design.md Decision 7
    in openspec/changes/release-3-0-0/) and would not appear in `sessions list`."""
    # Mirrors transcript_dir_for_project()'s encoding directly rather than calling it: that
    # helper resolves against THIS process's Path.home(), not the subprocess's HOME env var
    # the migration actually runs under (see base_env).
    encoded = str(project_dir).replace("/", "-").replace(".", "-")
    transcript_dir = home / ".claude" / "projects" / encoded
    transcript_dir.mkdir(parents=True, exist_ok=True)
    record = {"type": "custom-title", "customTitle": basename, "sessionId": uuid}
    (transcript_dir / f"{uuid}.jsonl").write_text(json.dumps(record) + "\n")


def test_sessions_migrate_then_list_shows_row(base_env, tmp_path):
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    (tags_dir / "uuid-1.tag").write_text("my-feature\n")
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    sess = proj / "cc-sessions" / "20260713-my-feature"
    (sess / "working").mkdir(parents=True)
    _write_transcript(Path(base_env["HOME"]), proj, "uuid-my-feature", "20260713-my-feature")

    r_migrate = _run(base_env, "sessions", "migrate", "--tags-dir", str(tags_dir))
    assert r_migrate.returncode == 0

    r_list = _run(base_env, "sessions", "list")
    assert r_list.returncode == 0
    assert "20260713-my-feature" in r_list.stdout


def test_sessions_list_json_output(base_env, tmp_path):
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    (proj / "cc-sessions" / "20260713-json-test" / "working").mkdir(parents=True)
    _write_transcript(Path(base_env["HOME"]), proj, "uuid-json-test", "20260713-json-test")
    _run(base_env, "sessions", "migrate")

    r = _run(base_env, "sessions", "list", "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert any(row["basename"] == "20260713-json-test" for row in data)
    assert any(row.get("uuid") == "uuid-json-test" for row in data)


def test_tags_noun_no_longer_exists(base_env):
    """D4: `ccst tags migrate` is retired."""
    r = _run(base_env, "tags", "migrate")
    assert r.returncode != 0


# ---------------------------------------------------------------------------
# `ccst sessions migrate-uuid` — the 3.0.0 schema migration, end to end via subprocess
# ---------------------------------------------------------------------------

def _seed_old_schema_db(db_path: Path, project_dir: str, basename: str) -> None:
    import sqlite3

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE sessions (project_dir TEXT NOT NULL, basename TEXT NOT NULL, "
        "start_date TEXT NOT NULL, last_opened REAL, last_active REAL, "
        "discovered_at TEXT NOT NULL, updated_at TEXT, "
        "PRIMARY KEY (project_dir, basename))"
    )
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES (?, ?, ?, ?)",
        (project_dir, basename, basename.split("-", 1)[0], "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()


def test_sessions_migrate_uuid_dry_run_does_not_write(base_env, tmp_path):
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    db_path = Path(base_env["CCST_SESSIONS_DIR"]) / "sessions.db"
    _seed_old_schema_db(db_path, str(proj), "20260101-old")

    r = _run(base_env, "sessions", "migrate-uuid")  # no flag -> dry-run
    assert r.returncode == 0
    assert "resolved" in r.stdout.lower() or "ambiguous" in r.stdout.lower()

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    pk_cols = {row[1]: row[5] for row in conn.execute("PRAGMA table_info(sessions)")}
    conn.close()
    assert "uuid" not in pk_cols


def test_sessions_migrate_uuid_write_migrates_and_list_shows_uuid(base_env, tmp_path):
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    db_path = Path(base_env["CCST_SESSIONS_DIR"]) / "sessions.db"
    _seed_old_schema_db(db_path, str(proj), "20260101-old")
    _write_transcript(Path(base_env["HOME"]), proj, "uuid-old", "20260101-old")

    r = _run(base_env, "sessions", "migrate-uuid", "--write")
    assert r.returncode == 0, r.stderr

    r_list = _run(base_env, "sessions", "list", "--json")
    data = json.loads(r_list.stdout)
    assert any(row["basename"] == "20260101-old" and row["uuid"] == "uuid-old" for row in data)
