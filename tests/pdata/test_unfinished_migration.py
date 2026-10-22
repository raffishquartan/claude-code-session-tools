"""A format 4 database that `ccst pdata migrate-uuid` converted, but whose run stopped before the
`pdata-uuid-ids` marker was recorded, has not been verified: nothing may publish or sync it until
`migrate-uuid --write` finishes the run. Isolated like test_sync_check.py.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import (
    dump,
    formats,
    migrate_uuid,
    rehydrate,
    store,
    sync_check,
)
from hooks import pdata_sync

from .legacy_fixture import make_format3_db, write_format3_dump

PROJECT = "p"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_MACHINE_NAME", "machine-a")
    monkeypatch.setenv("CCST_HOOKS_DIR", str(tmp_path / "hooks"))
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("CCCS_CREDS_PATH", str(tmp_path / "no-creds-here"))
    monkeypatch.setattr(migrate_uuid.occupancy, "is_occupied", lambda *a, **k: False)
    monkeypatch.setattr(sync_check.occupancy, "is_occupied", lambda *a, **k: False)
    monkeypatch.setattr(pdata_sync.occupancy, "is_occupied", lambda *a, **k: False)
    monkeypatch.setattr(sync_check.sync_notify, "notify_conflict", lambda *a, **k: None)
    monkeypatch.setattr(pdata_sync.sync_notify, "notify_conflict", lambda *a, **k: None)


@pytest.fixture
def unfinished() -> Path:
    """A converted database whose run stopped after the rewrite: no marker, no format 4 dump."""
    path = make_format3_db(store.db_path(PROJECT))
    project_root = store.project_root(PROJECT)
    write_format3_dump(path, project_root, machine_id="machine-a", vector={"machine-a": 3})
    assert migrate_uuid.migrate_project(PROJECT, write=True).outcome is (
        migrate_uuid.Outcome.MIGRATED
    )
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DELETE FROM migrations WHERE name=?", (formats.UUID_IDS_MIGRATION,))
        conn.commit()
    finally:
        conn.close()
    directory = dump.dump_dir(project_root)
    for name in ("latest.sql", "latest.sha256"):
        (directory / name).unlink()
    return path


def test_format_gate_holds_an_unfinished_migration(unfinished: Path) -> None:
    root = store.project_root(PROJECT)
    pending = rehydrate.format_gate(PROJECT, project_root=root, existing=dump.read_latest(root))
    assert pending is not None
    assert pending.outcome is rehydrate.RehydrateOutcome.MIGRATION_PENDING
    assert pending.unfinished_migration
    detail = rehydrate.migration_pending_detail(pending, project=PROJECT)
    assert "did not finish" in detail
    assert f"{formats.MIGRATE_COMMAND} --project {PROJECT} --write" in detail


def test_sync_check_publishes_nothing_until_the_marker_is_recorded(unfinished: Path) -> None:
    result = sync_check.check_project(PROJECT)
    assert result.outcome is sync_check.SyncOutcome.MIGRATION_PENDING
    assert not dump.latest_path(store.project_root(PROJECT)).exists()

    finished = migrate_uuid.migrate_project(PROJECT, write=True)
    assert finished.outcome is migrate_uuid.Outcome.RESUMED, migrate_uuid.render(finished)
    assert sync_check.check_project(PROJECT).outcome is sync_check.SyncOutcome.UNCHANGED


def test_session_end_publishes_nothing(unfinished: Path) -> None:
    pdata_sync.on_session_end(str(store.project_root(PROJECT)))
    assert not dump.latest_path(store.project_root(PROJECT)).exists()


def test_a_database_created_as_format4_without_the_marker_is_not_held() -> None:
    """Only a converted database (one with a migration origin) waits for the marker."""
    from cc_session_tools.lib.pdata import repository

    conn = repository.connect(PROJECT)
    try:
        conn.execute("DELETE FROM migrations")
    finally:
        conn.close()
    root = store.project_root(PROJECT)
    assert rehydrate.format_gate(PROJECT, project_root=root, existing=dump.read_latest(root)) is None


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=env,
    )


def test_cli_dump_refuses_and_migrate_uuid_finishes(unfinished: Path, tmp_path: Path) -> None:
    env = os.environ.copy()
    for name in (store.PROJECT_DB_DIR_ENV, store.PROJECTS_ROOT_ENV, "CCST_MACHINE_NAME",
                 "CCST_HOOKS_DIR", "CC_SCHEDULER_DIR", "CCCS_CREDS_PATH"):
        env[name] = os.environ[name]
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("TELEGRAM_CHAT_ID", None)

    r = _run(env, "pdata", "dump", "--project", PROJECT)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert f"{PROJECT}: the database was converted to format 4" in r.stderr
    assert f"{formats.MIGRATE_COMMAND} --project {PROJECT} --write" in r.stderr
    assert not dump.latest_path(store.project_root(PROJECT)).exists()

    r = _run(env, "pdata", "migrate-uuid", "--project", PROJECT, "--write")
    assert r.returncode == 0, (r.stdout, r.stderr)
    r = _run(env, "pdata", "dump", "--project", PROJECT)
    assert r.returncode == 0, (r.stdout, r.stderr)
