"""CLI tests for `ccst pdata migrate-uuid` (task 5.4): flags, exit codes and output. The
migration rules themselves are tested in tests/pdata/test_migrate_uuid.py."""
from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import formats, migrate_uuid, repository, store

from pdata.legacy_fixture import make_format3_db, snapshot_dir, write_format3_dump


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "pdata", "migrate-uuid", *args],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=env,
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_MACHINE_NAME", "machine-a")
    monkeypatch.setenv("CCST_HOOKS_DIR", str(tmp_path / "hooks"))
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("CCCS_CREDS_PATH", str(tmp_path / "no-creds-here"))
    # The occupancy precondition shells out to `pgrep -x claude`; a stub that finds nothing
    # keeps these subprocess runs deterministic inside a live `claude` session.
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    pgrep = bin_dir / "pgrep"
    pgrep.write_text("#!/bin/sh\nexit 1\n")
    pgrep.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return os.environ.copy()


def _converged(project: str, *, dump_vector: dict[str, int] | None = None) -> Path:
    path = make_format3_db(store.db_path(project))
    write_format3_dump(
        path, store.project_root(project), machine_id="machine-a",
        vector=dump_vector or {"machine-a": 3},
    )
    return path


def test_target_is_required(env: dict[str, str]) -> None:
    result = _run(env)
    assert result.returncode == 2
    assert "--project" in result.stderr


def test_dry_run_and_write_are_mutually_exclusive(env: dict[str, str]) -> None:
    result = _run(env, "--project", "p", "--dry-run", "--write")
    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr


def test_the_default_is_a_dry_run(env: dict[str, str], tmp_path: Path) -> None:
    _converged("p")
    before = snapshot_dir(tmp_path / "project-db")
    result = _run(env, "--project", "p")
    assert result.returncode == 0, result.stderr
    assert "p: dry run - nothing was changed" in result.stdout
    assert "notes: 2 (1 soft-deleted)" in result.stdout
    assert "re-run with --write" in result.stdout
    assert snapshot_dir(tmp_path / "project-db") == before


def test_explicit_dry_run_matches_the_default(env: dict[str, str]) -> None:
    _converged("p")
    assert _run(env, "--project", "p", "--dry-run").stdout == _run(env, "--project", "p").stdout


def test_a_dry_run_with_an_unmet_precondition_exits_1(env: dict[str, str]) -> None:
    _converged("p", dump_vector={"machine-a": 2})
    result = _run(env, "--project", "p")
    assert result.returncode == 1
    assert "[NO] converged with the published format 3 dump" in result.stderr
    assert formats.LAST_FORMAT3_RELEASE in result.stderr


def test_dry_run_lists_fields_that_suggest_ids(env: dict[str, str]) -> None:
    path = _converged("p")
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("ALTER TABLE ext_notes ADD COLUMN parent_id INTEGER")
        conn.commit()
    finally:
        conn.close()
    write_format3_dump(path, store.project_root("p"), machine_id="machine-a",
                       vector={"machine-a": 3})
    result = _run(env, "--project", "p")
    assert "ext_notes: extension field parent_id" in result.stdout


def test_write_migrates_and_names_the_backup(env: dict[str, str]) -> None:
    path = _converged("p")
    result = _run(env, "--project", "p", "--write")
    assert result.returncode == 0, result.stderr
    assert "p: migrated to format 4" in result.stdout
    assert "migration-backups/pdata-p-pre-4.0.0-" in result.stdout
    assert formats.database_format(path) == formats.PDATA_FORMAT_UUID_IDS

    again = _run(env, "--project", "p", "--write")
    assert again.returncode == 0
    assert "already migrated" in again.stdout


def test_a_refused_write_exits_1_and_changes_nothing(env: dict[str, str], tmp_path: Path) -> None:
    _converged("p", dump_vector={"machine-a": 2, "machine-b": 1})
    before = snapshot_dir(tmp_path / "project-db")
    result = _run(env, "--project", "p", "--write")
    assert result.returncode == 1
    assert "refused - nothing was changed" in result.stderr
    assert snapshot_dir(tmp_path / "project-db") == before


def test_a_project_with_no_database_exits_0(env: dict[str, str]) -> None:
    result = _run(env, "--project", "nothing-here", "--write")
    assert result.returncode == 0
    assert "nothing to migrate" in result.stdout


def test_all_projects_with_no_databases_exits_0(env: dict[str, str]) -> None:
    result = _run(env, "--all-projects")
    assert result.returncode == 0
    assert "no project databases found" in result.stdout


def test_all_projects_reports_every_project(env: dict[str, str]) -> None:
    _converged("legacy")
    conn = repository.connect("fresh")
    conn.close()
    result = _run(env, "--all-projects", "--write")
    assert result.returncode == 0, result.stderr
    assert "fresh: already migrated" in result.stdout
    assert "legacy: migrated to format 4" in result.stdout


def test_an_invalid_project_name_exits_2(env: dict[str, str]) -> None:
    result = _run(env, "--project", "../escape")
    assert result.returncode == 2
    assert "invalid project name" in result.stderr


def test_a_failed_migration_exits_2(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from cc_session_tools.cli import ccst

    def failed(project: str, *, write: bool) -> migrate_uuid.MigrationResult:
        return migrate_uuid.MigrationResult(
            project=project, outcome=migrate_uuid.Outcome.FAILED,
            messages=("migration failed: simulated",),
        )

    monkeypatch.setattr(migrate_uuid, "migrate_project", failed)
    args = argparse.Namespace(project="p", all_projects=False, write=True, dry_run=False)
    assert ccst._cmd_pdata_migrate_uuid(args) == 2
    assert "p: FAILED" in capsys.readouterr().err


def test_help_names_the_flags(env: dict[str, str]) -> None:
    result = _run(env, "--help")
    assert result.returncode == 0
    for flag in ("--project", "--all-projects", "--dry-run", "--write"):
        assert flag in result.stdout
