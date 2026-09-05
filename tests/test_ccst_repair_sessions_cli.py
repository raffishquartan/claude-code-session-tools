"""CLI tests for `ccst repair sessions` — see tests/test_ccst_pdata_verify_cli.py for the
base_env/_run convention this mirrors, extended with CCST_SESSIONS_DIR and a roots env var."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path):
    env = os.environ.copy()
    env["CCST_PROJECT_DB_DIR"] = str(tmp_path / "project-db")
    env["CCST_PROJECTS_ROOT"] = str(tmp_path / "projects")
    env["CCST_SESSIONS_DIR"] = str(tmp_path / "sessions-dir")
    repos_root = tmp_path / "repos"
    repos_root.mkdir(parents=True, exist_ok=True)
    env["CLAUDE_SESSION_TOOLS_REPO_ROOT"] = str(repos_root)
    return env


def _sessions_db_path(env: dict) -> Path:
    return Path(env["CCST_SESSIONS_DIR"]) / "sessions.db"


def _seed_bad_row(sessions_db_path: Path, repos_root: Path) -> None:
    """Write a sessions.db row with a non-absolute project_dir, plus the on-disk
    cc-sessions/<basename>/ directory that a repair needs to resolve it — via
    sessions_db.connect() so this always matches the real schema rather than a
    hand-rolled CREATE TABLE that could drift from it."""
    from cc_session_tools.lib import sessions_db

    (repos_root / "myproj" / "cc-sessions" / "20260101-bad").mkdir(parents=True)
    conn = sessions_db.connect(path=sessions_db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, uuid, start_date, discovered_at) "
        "VALUES ('.', '20260101-bad', 'uuid-20260101-bad', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()


def test_repair_dry_run_reports_without_modifying(base_env):
    from cc_session_tools.lib import sessions_db

    db_path = _sessions_db_path(base_env)
    repos_root = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"])
    _seed_bad_row(db_path, repos_root)

    # No --execute -> dry-run is the default; nothing on disk changes.
    r = _run(base_env, "repair", "sessions")
    assert r.returncode == 0, r.stderr
    assert "20260101-bad" in r.stdout

    rows = sessions_db.list_sessions(path=db_path)
    row = next(row for row in rows if row.basename == "20260101-bad")
    assert str(row.project_dir) == "."


def test_repair_explicit_dry_run_flag_matches_default_behaviour(base_env):
    """The doctor WARN and every other Task 1-3 diagnostic tells the user to run
    'ccst repair sessions --dry-run' — that flag must parse and behave exactly like
    passing no mode flag at all, not error with 'unrecognized arguments'."""
    from cc_session_tools.lib import sessions_db

    db_path = _sessions_db_path(base_env)
    repos_root = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"])
    _seed_bad_row(db_path, repos_root)

    r_explicit = _run(base_env, "repair", "sessions", "--dry-run")
    assert r_explicit.returncode == 0, r_explicit.stderr
    assert "20260101-bad" in r_explicit.stdout

    rows = sessions_db.list_sessions(path=db_path)
    row = next(row for row in rows if row.basename == "20260101-bad")
    assert str(row.project_dir) == "."

    r_implicit = _run(base_env, "repair", "sessions")
    assert r_implicit.returncode == r_explicit.returncode
    assert r_implicit.stdout == r_explicit.stdout


def test_repair_dry_run_and_execute_together_is_a_parse_error(base_env):
    r = _run(base_env, "repair", "sessions", "--dry-run", "--execute")
    assert r.returncode == 2
    assert "not allowed with" in r.stderr


def test_repair_execute_against_nonexistent_db_errors_without_creating_backup(base_env):
    """sqlite3.connect() auto-creates an empty file — without an explicit exists() check,
    --execute against a sessions.db that was never created would silently back up and
    'repair' an empty file instead of failing loudly on the mistake."""
    db_path = _sessions_db_path(base_env)
    assert not db_path.exists()

    r = _run(base_env, "repair", "sessions", "--execute")
    assert r.returncode != 0
    assert str(db_path) in r.stderr

    assert not db_path.exists()
    assert not (db_path.parent / "repair-backups").exists()


def test_repair_dry_run_against_corrupt_db_fails_cleanly(base_env):
    """Found during the install-sync-nudge branch's final review: this is
    the exact command a `ccst doctor`/nudge message points users at to
    investigate store corruption - it must fail with a clear message, not a
    raw sqlite3 traceback. sqlite3.connect() opens lazily and only fails
    once sessions_repair.repair() actually queries the file, so the
    dry-run-default path (no --execute, so the earlier exists()-before-
    backup guard never runs) is the one that needs its own guard."""
    db_path = _sessions_db_path(base_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"not a sqlite database file")

    r = _run(base_env, "repair", "sessions")

    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert str(db_path) in r.stderr
    assert "failed to open" in r.stderr


def test_repair_execute_against_corrupt_db_fails_cleanly(base_env):
    """A second, distinct crash site found in the same round: --execute's
    backup step (db_lib.backup_to(), SQLite's own online backup API) also
    opens lazily and only fails once the backup actually touches the file -
    a different call site than the dry-run test above, reached only in
    --execute mode. Both must be covered; fixing only the dry-run path
    would leave the actual fix action of this recovery tool crashing."""
    db_path = _sessions_db_path(base_env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"not a sqlite database file")

    r = _run(base_env, "repair", "sessions", "--execute")

    assert r.returncode != 0
    assert "Traceback" not in r.stderr
    assert str(db_path) in r.stderr
    assert "failed to open" in r.stderr


def test_repair_execute_updates_row_and_backs_up_first(base_env):
    from cc_session_tools.lib import sessions_db

    db_path = _sessions_db_path(base_env)
    repos_root = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"])
    _seed_bad_row(db_path, repos_root)

    r = _run(base_env, "repair", "sessions", "--execute")
    assert r.returncode == 0, r.stderr

    rows = sessions_db.list_sessions(path=db_path)
    row = next(row for row in rows if row.basename == "20260101-bad")
    assert row.project_dir.is_absolute()
    assert row.project_dir.name == "myproj"

    backup_dir = db_path.parent / "repair-backups"
    assert backup_dir.is_dir()
    backups = list(backup_dir.glob("sessions-*.db"))
    assert backups

    # The backup must be a valid, readable SQLite DB that captures PRE-repair state —
    # i.e. taken before repair() writes, not after (and not a silent no-op copy).
    backup_conn = sqlite3.connect(str(backups[0]))
    try:
        backup_row = backup_conn.execute(
            "SELECT project_dir FROM sessions WHERE basename = ?", ("20260101-bad",)
        ).fetchone()
    finally:
        backup_conn.close()
    assert backup_row is not None
    assert backup_row[0] == "."


# ---------------------------------------------------------------------------
# `ccst repair sessions --uuid` — sessions_migration_ambiguous inspection/resolution
# ---------------------------------------------------------------------------

def test_repair_sessions_uuid_no_ambiguous_rows(base_env):
    r = _run(base_env, "repair", "sessions", "--uuid")
    assert r.returncode == 0
    assert "No migration-ambiguous" in r.stdout


def test_repair_sessions_uuid_dry_run_lists_rows(base_env):
    from cc_session_tools.lib import sessions_db

    db_path = _sessions_db_path(base_env)
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    sessions_db.record_ambiguous_row(
        proj, "20260101-mystery", "no-transcript-found",
        start_date="20260101", discovered_at="2026-01-01T00:00:00Z", path=db_path,
    )

    r = _run(base_env, "repair", "sessions", "--uuid")
    assert r.returncode == 0
    assert "20260101-mystery" in r.stdout
    assert "no-transcript-found" in r.stdout
    # Dry-run must not have touched the sidecar.
    assert len(sessions_db.list_ambiguous_rows(path=db_path)) == 1


def test_repair_sessions_uuid_execute_resolves_when_transcript_now_exists(base_env, tmp_path):
    from cc_session_tools.lib import sessions_db

    home = tmp_path / "home"
    home.mkdir()
    base_env["HOME"] = str(home)
    db_path = _sessions_db_path(base_env)
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    sessions_db.record_ambiguous_row(
        proj, "20260101-found-now", "no-transcript-found",
        start_date="20260101", discovered_at="2026-01-01T00:00:00Z", path=db_path,
    )
    # A transcript has since appeared for this basename.
    encoded = str(proj).replace("/", "-").replace(".", "-")
    transcript_dir = home / ".claude" / "projects" / encoded
    transcript_dir.mkdir(parents=True)
    (transcript_dir / "uuid-found.jsonl").write_text(
        '{"type": "custom-title", "customTitle": "20260101-found-now", "sessionId": "uuid-found"}\n'
    )

    r = _run(base_env, "repair", "sessions", "--uuid", "--execute")
    assert r.returncode == 0, r.stderr
    assert "resolved" in r.stdout.lower()

    assert sessions_db.list_ambiguous_rows(path=db_path) == []
    rows = sessions_db.list_sessions(path=db_path)
    assert any(r_.basename == "20260101-found-now" and r_.uuid == "uuid-found" for r_ in rows)


def test_repair_sessions_uuid_execute_leaves_still_unresolvable_rows(base_env):
    from cc_session_tools.lib import sessions_db

    db_path = _sessions_db_path(base_env)
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    sessions_db.record_ambiguous_row(
        proj, "20260101-still-nothing", "no-transcript-found",
        start_date="20260101", discovered_at="2026-01-01T00:00:00Z", path=db_path,
    )

    r = _run(base_env, "repair", "sessions", "--uuid", "--execute")
    assert r.returncode == 0
    assert "still ambiguous" in r.stderr.lower()
    assert len(sessions_db.list_ambiguous_rows(path=db_path)) == 1


def test_repair_sessions_uuid_forget_deletes_row(base_env):
    from cc_session_tools.lib import sessions_db

    db_path = _sessions_db_path(base_env)
    proj = Path(base_env["CLAUDE_SESSION_TOOLS_REPO_ROOT"]) / "myproj"
    sessions_db.record_ambiguous_row(
        proj, "20260101-forget-me", "no-transcript-found",
        start_date="20260101", discovered_at="2026-01-01T00:00:00Z", path=db_path,
    )

    r = _run(base_env, "repair", "sessions", "--uuid", "--forget", str(proj), "20260101-forget-me")
    assert r.returncode == 0
    assert "Forgot" in r.stdout
    assert sessions_db.list_ambiguous_rows(path=db_path) == []


def test_repair_sessions_uuid_forget_reports_error_when_absent(base_env):
    r = _run(
        base_env, "repair", "sessions", "--uuid", "--forget", "/repos/nope", "20260101-nope"
    )
    assert r.returncode == 1
    assert "No ambiguous row found" in r.stderr
