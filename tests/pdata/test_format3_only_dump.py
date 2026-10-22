"""A machine with no database for a project whose only published dump is format 3 must create
nothing: a fresh format 4 database published from there would be a format 4 dump with no
migration lineage, and the machine holding the real data could then never migrate (design D5).

Every path that could create the database is covered: rehydrate, the hourly sync-check, the
session-output reconcile job, the record service, and the CLI gate. Isolated like
test_sync_check.py: notifications are captured, occupancy is pinned to False.
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import (
    dump,
    formats,
    migrate_uuid,
    rehydrate,
    service,
    session_output,
    store,
    sync_check,
)

from .legacy_fixture import make_format3_db, write_format3_dump

PROJECT = "p"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "machine-a" / "project-db"))
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_MACHINE_NAME", "machine-a")
    monkeypatch.setenv("CCST_HOOKS_DIR", str(tmp_path / "hooks"))
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("CCCS_CREDS_PATH", str(tmp_path / "no-creds-here"))
    monkeypatch.setattr(migrate_uuid.occupancy, "is_occupied", lambda *a, **k: False)
    monkeypatch.setattr(sync_check.occupancy, "is_occupied", lambda *a, **k: False)
    monkeypatch.setattr(sync_check.sync_notify, "notify_conflict", lambda *a, **k: None)


@contextmanager
def on_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> Iterator[None]:
    with monkeypatch.context() as m:
        m.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / name / "project-db"))
        m.setenv("CCST_MACHINE_NAME", name)
        yield


def _format3_published() -> Path:
    """Machine A holds a format 3 database, converged with its published format 3 dump."""
    path = make_format3_db(store.db_path(PROJECT))
    write_format3_dump(
        path, store.project_root(PROJECT), machine_id="machine-a", vector={"machine-a": 3},
    )
    return path


def _assert_machine_a_can_still_migrate() -> None:
    assert not dump.latest_path(store.project_root(PROJECT)).parent.exists()
    result = migrate_uuid.migrate_project(PROJECT, write=False)
    assert result.outcome is migrate_uuid.Outcome.PLANNED, migrate_uuid.render(result)


def test_format_gate_reports_a_missing_database_beside_a_format3_dump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _format3_published()
    with on_machine(monkeypatch, tmp_path, "machine-b"):
        root = store.project_root(PROJECT)
        pending = rehydrate.format_gate(PROJECT, project_root=root, existing=dump.read_latest(root))
        assert pending is not None
        assert pending.outcome is rehydrate.RehydrateOutcome.MIGRATION_PENDING
        assert pending.local_format is None
        assert pending.dump_format == formats.PDATA_FORMAT_INTEGER_IDS
        detail = rehydrate.migration_pending_detail(pending, project=PROJECT)
        assert "no database" in detail
        assert f"{formats.MIGRATE_COMMAND} --project {PROJECT}" in detail


def test_rehydrate_creates_no_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _format3_published()
    with on_machine(monkeypatch, tmp_path, "machine-b"):
        result = rehydrate.rehydrate(PROJECT, force=True)
        assert result.outcome is rehydrate.RehydrateOutcome.MIGRATION_PENDING
        assert not store.db_path(PROJECT).exists()
    _assert_machine_a_can_still_migrate()


def test_sync_check_creates_and_publishes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _format3_published()
    with on_machine(monkeypatch, tmp_path, "machine-b"):
        result = sync_check.check_project(PROJECT)
        assert result.outcome is sync_check.SyncOutcome.MIGRATION_PENDING
        assert result.format3_status is None
        assert not store.db_path(PROJECT).exists()
    _assert_machine_a_can_still_migrate()


def test_the_record_service_refuses_to_create_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _format3_published()
    with on_machine(monkeypatch, tmp_path, "machine-b"):
        with pytest.raises(formats.AwaitingMigrationError, match="only published dump is format 3"):
            service.add_record(
                project=PROJECT, record_group="notes", content="x", file_path=None, fields={},
            )
        assert not store.db_path(PROJECT).exists()
        assert sync_check.check_project(PROJECT).outcome is sync_check.SyncOutcome.MIGRATION_PENDING
    _assert_machine_a_can_still_migrate()


def test_the_session_output_reconcile_job_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _format3_published()
    out = store.project_root(PROJECT) / "cc-sessions" / "20261020-x" / "out"
    out.mkdir(parents=True)
    (out / "f.md").write_text("x")
    with on_machine(monkeypatch, tmp_path, "machine-b"):
        with pytest.raises(formats.AwaitingMigrationError):
            session_output.reconcile_project(PROJECT, store.project_root(PROJECT))
        assert not store.db_path(PROJECT).exists()
    _assert_machine_a_can_still_migrate()


def test_a_format4_dump_beside_the_format3_one_is_loaded_as_usual(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Once a machine has migrated and published the format 4 dump, a machine with no database
    rehydrates from it as for any other project."""
    _format3_published()
    assert migrate_uuid.migrate_project(PROJECT, write=True).outcome is migrate_uuid.Outcome.MIGRATED
    with on_machine(monkeypatch, tmp_path, "machine-b"):
        result = rehydrate.rehydrate(PROJECT)
        assert result.outcome is rehydrate.RehydrateOutcome.FAST_FORWARDED
        assert formats.database_format(store.db_path(PROJECT)) == formats.PDATA_FORMAT_UUID_IDS


# ------------------------------------------------------------------ the CLI gate


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=env,
    )


@pytest.fixture
def machine_b_env(tmp_path: Path) -> dict[str, str]:
    """Machine A's format 3 dump in the shared project root (with a session-output file to
    register), and the environment of machine B, which has no database for the project."""
    projects_root = tmp_path / "projects"
    a_db = make_format3_db(tmp_path / "machine-a" / "project-db" / f"{PROJECT}.db")
    write_format3_dump(
        a_db, projects_root / PROJECT, machine_id="machine-a", vector={"machine-a": 3},
    )
    out = projects_root / PROJECT / "cc-sessions" / "20261020-x" / "out"
    out.mkdir(parents=True)
    (out / "f.md").write_text("x")
    env = os.environ.copy()
    env["CCST_PROJECT_DB_DIR"] = str(tmp_path / "machine-b" / "project-db")
    env["CCST_PROJECTS_ROOT"] = str(projects_root)
    env["CLAUDE_SESSION_TOOLS_PROJ_ROOT"] = str(projects_root)
    env["CCST_MACHINE_NAME"] = "machine-b"
    env["CCST_HOOKS_DIR"] = str(tmp_path / "hooks")
    env["CC_SCHEDULER_DIR"] = str(tmp_path / "sched")
    env["CCCS_CREDS_PATH"] = str(tmp_path / "no-creds-here")
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("TELEGRAM_CHAT_ID", None)
    return env


@pytest.mark.parametrize("argv", [
    ("add", "--project", PROJECT, "--group", "notes", "--content", "x"),
    ("list", "--project", PROJECT, "--group", "notes"),
    ("schema", "add-field", "--project", PROJECT, "--group", "notes", "--field", "f:TEXT"),
    ("reconcile-session-output", "--project", PROJECT),
    ("reconcile-session-output", "--project", PROJECT, "--schema-only"),
    ("reconcile-session-output", "--all-projects"),
    ("dump", "--project", PROJECT),
    ("rehydrate", "--project", PROJECT),
], ids=lambda a: " ".join(a[:2]))
def test_cli_verbs_refuse_to_create_the_database(
    machine_b_env: dict[str, str], tmp_path: Path, argv: tuple[str, ...],
) -> None:
    r = _run(machine_b_env, "pdata", *argv)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert f"{PROJECT}: this machine has no database" in r.stderr
    assert f"{formats.MIGRATE_COMMAND} --project {PROJECT}" in r.stderr
    assert not (tmp_path / "machine-b" / "project-db" / f"{PROJECT}.db").exists()
    assert not (tmp_path / "projects" / PROJECT / dump.DUMP_ROOT_DIR_NAME / "v4").exists()


def test_migration_pending_detail_refuses_any_other_outcome() -> None:
    result = rehydrate.RehydrateResult(outcome=rehydrate.RehydrateOutcome.NO_OP)
    with pytest.raises(ValueError, match="not a migration-pending outcome: no_op"):
        rehydrate.migration_pending_detail(result, project=PROJECT)
