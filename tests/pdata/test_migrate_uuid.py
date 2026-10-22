"""Tests for `lib/pdata/migrate_uuid.py` - `ccst pdata migrate-uuid` (design D2, D4, D6, D6c, D9).

Every database here is built in tmp_path. `occupancy.is_occupied` is pinned to False by an autouse
fixture: it shells out to `pgrep -x claude`, and this suite is routinely run from inside a live
`claude` session.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from cc_session_tools.lib import db
from cc_session_tools.lib.pdata import (
    dump,
    formats,
    ids,
    migrate_uuid,
    repository,
    store,
    vector_clock_store,
    verify,
)

from .legacy_fixture import make_format3_db, snapshot_dir, write_format3_dump

PROJECT = "p"
VECTOR = {"machine-a": 3}


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "machine-a" / "project-db"))
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_MACHINE_NAME", "machine-a")


@pytest.fixture(autouse=True)
def unoccupied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        migrate_uuid.occupancy, "is_occupied", lambda root, *, exclude_pid=None: False
    )


@contextmanager
def on_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> Iterator[None]:
    """Run the body as machine `name`: its own database directory, the shared project root."""
    with monkeypatch.context() as m:
        m.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / name / "project-db"))
        m.setenv("CCST_MACHINE_NAME", name)
        yield


def _converged_project(*, dump_vector: dict[str, int] | None = None) -> Path:
    """A format 3 database for PROJECT with its format 3 dump published at `dump_vector`
    (default: the database's own vector, i.e. converged). Returns the database path."""
    path = make_format3_db(store.db_path(PROJECT))
    write_format3_dump(
        path, store.project_root(PROJECT), machine_id="machine-a",
        vector=VECTOR if dump_vector is None else dump_vector,
    )
    return path


def _ids(path: Path, table: str = "records", column: str = "id") -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return sorted(str(r[0]) for r in conn.execute(f'SELECT {column} FROM "{table}"'))
    finally:
        conn.close()


def _mapped(*old_ids: int) -> list[str]:
    return sorted(ids.deterministic_record_id(PROJECT, str(i)) for i in old_ids)


def _everything(tmp_path: Path) -> dict[str, bytes]:
    """Every file under tmp_path except SQLite's -wal/-shm sidecars: opening a WAL database at
    all, even read-only, creates them, and they hold no data once the last writer closed."""
    return {
        name: data for name, data in snapshot_dir(tmp_path).items()
        if not name.endswith(("-wal", "-shm"))
    }


# ------------------------------------------------------------------ 5.1 plan and preconditions


def test_a_project_with_no_database_has_nothing_to_migrate() -> None:
    result = migrate_uuid.migrate_project(PROJECT, write=True)
    assert result.outcome is migrate_uuid.Outcome.NOTHING_TO_MIGRATE
    assert not store.db_path(PROJECT).exists()


def test_dry_run_reports_the_plan_and_changes_nothing(tmp_path: Path) -> None:
    _converged_project()
    before = _everything(tmp_path)

    result = migrate_uuid.migrate_project(PROJECT, write=False)

    assert result.outcome is migrate_uuid.Outcome.PLANNED
    assert result.plan is not None
    assert result.plan.ready
    assert result.plan.group_counts == (migrate_uuid.GroupCount("notes", rows=2, deleted=1),)
    assert result.plan.extension_tables == (("ext_notes", 2),)
    assert result.plan.watermark_rows == 0
    assert result.plan.path is migrate_uuid.MigrationPath.REWRITE
    assert all(p.holds for p in result.plan.preconditions)
    assert _everything(tmp_path) == before


@pytest.mark.parametrize(
    ("dump_vector", "status", "expected"),
    [
        ({"machine-a": 2}, "unpublished", ["unpublished", formats.LAST_FORMAT3_RELEASE,
                                           "sync-check", "unchanged"]),
        ({"machine-a": 4}, "behind", ["behind", formats.LAST_FORMAT3_RELEASE, "sync-check"]),
        ({"machine-a": 2, "machine-b": 1}, "fork", ["fork", "ccst pdata resolve",
                                                    formats.LAST_FORMAT3_RELEASE]),
    ],
)
def test_write_refuses_a_database_not_converged_with_its_format3_dump(
    tmp_path: Path, dump_vector: dict[str, int], status: str, expected: list[str],
) -> None:
    _converged_project(dump_vector=dump_vector)
    before = _everything(tmp_path)

    result = migrate_uuid.migrate_project(PROJECT, write=True)

    assert result.outcome is migrate_uuid.Outcome.REFUSED
    text = "\n".join(migrate_uuid.render(result))
    for fragment in expected:
        assert fragment in text
    assert _everything(tmp_path) == before


def test_write_refuses_a_database_whose_format3_dump_was_never_published(tmp_path: Path) -> None:
    make_format3_db(store.db_path(PROJECT))
    before = _everything(tmp_path)
    result = migrate_uuid.migrate_project(PROJECT, write=True)
    assert result.outcome is migrate_uuid.Outcome.REFUSED
    text = "\n".join(migrate_uuid.render(result))
    assert "never been published" in text
    assert formats.LAST_FORMAT3_RELEASE in text
    assert _everything(tmp_path) == before


def test_write_refuses_a_format3_dump_that_fails_its_checksum(tmp_path: Path) -> None:
    _converged_project()
    sha = dump.dump_dir(
        store.project_root(PROJECT), pdata_format=formats.PDATA_FORMAT_INTEGER_IDS,
    ) / "latest.sha256"
    sha.write_text("0" * 64)
    before = _everything(tmp_path)
    result = migrate_uuid.migrate_project(PROJECT, write=True)
    assert result.outcome is migrate_uuid.Outcome.REFUSED
    assert "checksum" in "\n".join(migrate_uuid.render(result))
    assert _everything(tmp_path) == before


def test_write_refuses_while_a_claude_session_occupies_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _converged_project()
    monkeypatch.setattr(
        migrate_uuid.occupancy, "is_occupied", lambda root, *, exclude_pid=None: True
    )
    before = _everything(tmp_path)
    result = migrate_uuid.migrate_project(PROJECT, write=True)
    assert result.outcome is migrate_uuid.Outcome.REFUSED
    assert "close every running `claude` session" in "\n".join(migrate_uuid.render(result))
    assert _everything(tmp_path) == before


def test_write_refuses_a_locked_database(tmp_path: Path) -> None:
    path = _converged_project()
    holder = sqlite3.connect(str(path), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        result = migrate_uuid.migrate_project(PROJECT, write=True)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert result.outcome is migrate_uuid.Outcome.REFUSED
    assert "another process is using" in "\n".join(migrate_uuid.render(result))
    assert formats.database_format(path) == formats.PDATA_FORMAT_INTEGER_IDS


def test_dry_run_with_an_unmet_precondition_is_refused_too() -> None:
    _converged_project(dump_vector={"machine-a": 2})
    result = migrate_uuid.migrate_project(PROJECT, write=False)
    assert result.outcome is migrate_uuid.Outcome.REFUSED
    assert result.plan is not None and not result.plan.ready


def test_dry_run_lists_fields_whose_names_suggest_record_ids() -> None:
    path = _converged_project()
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("ALTER TABLE ext_notes ADD COLUMN parent_id INTEGER")
        conn.execute("ALTER TABLE ext_notes ADD COLUMN ref TEXT")
        conn.execute(
            "INSERT INTO record_group_fields (record_group, field_name, added_at) "
            "VALUES ('notes', 'id', 1)"
        )
        conn.execute("UPDATE records SET content='{\"order_id\": 7, \"title\": \"x\"}' WHERE id=1")
        conn.commit()
    finally:
        conn.close()
    write_format3_dump(path, store.project_root(PROJECT), machine_id="machine-a", vector=VECTOR)

    result = migrate_uuid.migrate_project(PROJECT, write=False)

    assert result.plan is not None
    assert result.plan.suspicious_fields == (
        "ext_notes: extension field parent_id",
        "ext_notes: extension field ref",
        "notes: content key order_id",
        "notes: described field id",
    )
    assert "parent_id" in "\n".join(migrate_uuid.render(result))


# ------------------------------------------------------------------ 5.2 the rewrite


def _add_watermark(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE pdata_verify_watermark (record_id INTEGER PRIMARY KEY, "
            "last_seen_version INTEGER NOT NULL, last_seen_updated_at INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO pdata_verify_watermark VALUES (1, 1, 1000)")
        conn.commit()
    finally:
        conn.close()


def test_write_rewrites_every_id_table_and_publishes_the_format4_dump(tmp_path: Path) -> None:
    path = make_format3_db(store.db_path(PROJECT))
    _add_watermark(path)
    project_root = store.project_root(PROJECT)
    write_format3_dump(path, project_root, machine_id="machine-a", vector=VECTOR)
    format3_dir = dump.dump_dir(project_root, pdata_format=formats.PDATA_FORMAT_INTEGER_IDS)
    format3_before = {
        name: (format3_dir / name).read_bytes() for name in ("latest.sql", "latest.sha256")
    }

    result = migrate_uuid.migrate_project(PROJECT, write=True)

    assert result.outcome is migrate_uuid.Outcome.MIGRATED, migrate_uuid.render(result)
    assert formats.database_format(path) == formats.PDATA_FORMAT_UUID_IDS
    assert _ids(path) == _mapped(1, 2)
    assert _ids(path, "ext_notes", "record_id") == _mapped(1, 2)
    assert _ids(path, "pdata_verify_watermark", "record_id")[0] in _mapped(1)

    conn = db.connect(path, readonly=True)
    try:
        second = conn.execute(
            "SELECT * FROM records WHERE id=?", (ids.deterministic_record_id(PROJECT, "2"),)
        ).fetchone()
        assert (second["content"], second["created_at"], second["updated_at"],
                second["version"], second["deleted_at"]) == ("second", 1001, 1001, 1, 1002)
        topic = conn.execute(
            "SELECT topic FROM ext_notes WHERE record_id=?",
            (ids.deterministic_record_id(PROJECT, "1"),),
        ).fetchone()["topic"]
        assert topic == "a"
        assert db.migration_applied(conn, formats.UUID_IDS_MIGRATION)
        assert vector_clock_store.read_migration_origin(conn) == VECTOR
        assert vector_clock_store.read_vector(conn) == VECTOR
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()

    published = dump.read_latest(project_root)
    assert published.checksum_valid and published.pdata_format == formats.PDATA_FORMAT_UUID_IDS
    assert published.migrated_from_format3_vector == VECTOR
    for name, data in format3_before.items():
        assert (format3_dir / name).read_bytes() == data

    assert result.backup_path is not None
    assert result.backup_path.parent == path.parent / "migration-backups"
    assert result.backup_path.name.startswith(f"pdata-{PROJECT}-pre-4.0.0-")
    assert formats.database_format(result.backup_path) == formats.PDATA_FORMAT_INTEGER_IDS
    assert _ids(result.backup_path) == ["1", "2"]
    assert str(result.backup_path) in "\n".join(migrate_uuid.render(result))


def test_the_rewritten_database_works_with_the_format4_service(tmp_path: Path) -> None:
    from cc_session_tools.lib.pdata import service

    _converged_project()
    migrate_uuid.migrate_project(PROJECT, write=True)
    added = service.add_record(
        project=PROJECT, record_group="notes", content="third", file_path=None, fields={},
    )
    rows = service.list_records(project=PROJECT, record_group="notes")
    assert [r.content for r in rows] == ["first", "third"]
    assert ids.is_record_id(added.id)


def test_two_copies_of_the_same_database_map_to_identical_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = []
    for machine in ("copy-1", "copy-2"):
        with on_machine(monkeypatch, tmp_path, machine), monkeypatch.context() as m:
            m.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path / machine / "projects"))
            _converged_project()
            assert migrate_uuid.migrate_project(PROJECT, write=True).outcome is (
                migrate_uuid.Outcome.MIGRATED
            )
            results.append(_ids(store.db_path(PROJECT)))
    assert results[0] == results[1] == _mapped(1, 2)


def test_a_failure_inside_the_rewrite_leaves_the_original_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _converged_project()
    original = _ids(path)

    def boom(conn: sqlite3.Connection, table: str, *, id_column: str) -> None:
        raise sqlite3.OperationalError("disk I/O error (simulated)")

    monkeypatch.setattr(migrate_uuid, "_rewrite_table", boom)
    result = migrate_uuid.migrate_project(PROJECT, write=True)

    assert result.outcome is migrate_uuid.Outcome.FAILED
    assert formats.database_format(path) == formats.PDATA_FORMAT_INTEGER_IDS
    assert _ids(path) == original
    text = "\n".join(migrate_uuid.render(result))
    assert "simulated" in text
    assert result.backup_path is not None and str(result.backup_path) in text
    assert not dump.read_latest(store.project_root(PROJECT)).present


def test_a_new_verify_failure_restores_the_original_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _converged_project()

    def failing_verify(*, project: str, full: bool = False) -> verify.VerifySummary:
        return verify.VerifySummary(
            project=project, run_at=1, full_scan=full, status="FAIL",
            issues=[verify.VerifyIssue(
                check="row-count-parity", severity="FAIL", record_group="notes",
                record_id=None, message="rows went missing (simulated)",
            )],
        )

    monkeypatch.setattr(migrate_uuid.verify, "run_verify", failing_verify)
    result = migrate_uuid.migrate_project(PROJECT, write=True)

    assert result.outcome is migrate_uuid.Outcome.FAILED
    assert formats.database_format(path) == formats.PDATA_FORMAT_INTEGER_IDS
    assert _ids(path) == ["1", "2"]
    assert "rows went missing" in "\n".join(migrate_uuid.render(result))
    assert not dump.read_latest(store.project_root(PROJECT)).present


def test_a_verify_failure_already_present_before_migrating_does_not_block_it(
    tmp_path: Path,
) -> None:
    """A FAIL that verify reports on the format 3 database too (here a record whose file_path
    does not resolve) is not caused by the migration and does not fail it."""
    path = make_format3_db(store.db_path(PROJECT))
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("UPDATE records SET file_path='missing.md' WHERE id=1")
        conn.commit()
    finally:
        conn.close()
    write_format3_dump(path, store.project_root(PROJECT), machine_id="machine-a", vector=VECTOR)

    result = migrate_uuid.migrate_project(PROJECT, write=True)

    assert result.outcome is migrate_uuid.Outcome.MIGRATED, migrate_uuid.render(result)


def test_a_publish_failure_restores_the_original_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _converged_project()

    def failing_publish(*args: object, **kwargs: object) -> None:
        raise OSError("no space left on device (simulated)")

    monkeypatch.setattr(migrate_uuid.dump, "write_latest", failing_publish)
    result = migrate_uuid.migrate_project(PROJECT, write=True)

    assert result.outcome is migrate_uuid.Outcome.FAILED
    assert formats.database_format(path) == formats.PDATA_FORMAT_INTEGER_IDS
    assert "no space left" in "\n".join(migrate_uuid.render(result))


# ------------------------------------------------------------------ 5.3 idempotency, resume, join


def test_a_second_run_reports_already_migrated_and_changes_nothing(tmp_path: Path) -> None:
    _converged_project()
    migrate_uuid.migrate_project(PROJECT, write=True)
    before = _everything(tmp_path)

    result = migrate_uuid.migrate_project(PROJECT, write=True)

    assert result.outcome is migrate_uuid.Outcome.ALREADY_MIGRATED
    assert "already migrated" in "\n".join(migrate_uuid.render(result))
    assert _everything(tmp_path) == before


def test_a_database_created_as_format4_is_already_migrated() -> None:
    conn = repository.connect(PROJECT)
    conn.close()
    result = migrate_uuid.migrate_project(PROJECT, write=True)
    assert result.outcome is migrate_uuid.Outcome.ALREADY_MIGRATED


def _drop_v4_dump(project_root: Path) -> None:
    directory = dump.dump_dir(project_root)
    for name in ("latest.sql", "latest.sha256"):
        (directory / name).unlink()


def test_a_rerun_completes_a_missing_marker_and_dump(tmp_path: Path) -> None:
    path = _converged_project()
    migrate_uuid.migrate_project(PROJECT, write=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DELETE FROM migrations WHERE name=?", (formats.UUID_IDS_MIGRATION,))
        conn.commit()
    finally:
        conn.close()
    project_root = store.project_root(PROJECT)
    _drop_v4_dump(project_root)
    ids_before = _ids(path)

    dry = migrate_uuid.migrate_project(PROJECT, write=False)
    assert dry.outcome is migrate_uuid.Outcome.PLANNED
    assert not dump.read_latest(project_root).present

    result = migrate_uuid.migrate_project(PROJECT, write=True)

    assert result.outcome is migrate_uuid.Outcome.RESUMED, migrate_uuid.render(result)
    conn = db.connect(path, readonly=True)
    try:
        assert db.migration_applied(conn, formats.UUID_IDS_MIGRATION)
    finally:
        conn.close()
    assert _ids(path) == ids_before
    assert dump.read_latest(project_root).migrated_from_format3_vector == VECTOR


def test_a_rerun_publishes_a_missing_dump_only(tmp_path: Path) -> None:
    path = _converged_project()
    migrate_uuid.migrate_project(PROJECT, write=True)
    project_root = store.project_root(PROJECT)
    _drop_v4_dump(project_root)

    result = migrate_uuid.migrate_project(PROJECT, write=True)

    assert result.outcome is migrate_uuid.Outcome.RESUMED
    assert "published" in "\n".join(migrate_uuid.render(result))
    assert "marker" not in "\n".join(migrate_uuid.render(result))
    assert dump.read_latest(project_root).present
    assert formats.database_format(path) == formats.PDATA_FORMAT_UUID_IDS


def _migrate_first_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with on_machine(monkeypatch, tmp_path, "machine-a"):
        _converged_project()
        assert migrate_uuid.migrate_project(PROJECT, write=True).outcome is (
            migrate_uuid.Outcome.MIGRATED
        )


def test_a_joining_machine_on_the_same_lineage_adopts_the_format4_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _migrate_first_machine(monkeypatch, tmp_path)
    with on_machine(monkeypatch, tmp_path, "machine-b"):
        b_path = make_format3_db(store.db_path(PROJECT))
        dry = migrate_uuid.migrate_project(PROJECT, write=False)
        assert dry.outcome is migrate_uuid.Outcome.PLANNED
        assert dry.plan is not None and dry.plan.path is migrate_uuid.MigrationPath.ADOPT

        result = migrate_uuid.migrate_project(PROJECT, write=True)

        assert result.outcome is migrate_uuid.Outcome.ADOPTED, migrate_uuid.render(result)
        assert formats.database_format(b_path) == formats.PDATA_FORMAT_UUID_IDS
        assert _ids(b_path) == _mapped(1, 2)
        assert result.backup_path is not None and result.backup_path.exists()
        conn = db.connect(b_path, readonly=True)
        try:
            assert db.migration_applied(conn, formats.UUID_IDS_MIGRATION)
            assert vector_clock_store.read_migration_origin(conn) == VECTOR
        finally:
            conn.close()
        assert migrate_uuid.migrate_project(PROJECT, write=True).outcome is (
            migrate_uuid.Outcome.ALREADY_MIGRATED
        )
    with on_machine(monkeypatch, tmp_path, "machine-a"):
        assert _ids(store.db_path(PROJECT)) == _ids(b_path)


def test_a_joining_machine_with_format3_changes_the_lineage_never_saw_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _migrate_first_machine(monkeypatch, tmp_path)
    with on_machine(monkeypatch, tmp_path, "machine-b"):
        b_path = make_format3_db(store.db_path(PROJECT))
        conn = sqlite3.connect(str(b_path))
        try:
            conn.execute(
                "INSERT INTO pdata_meta (machine_id, revision, updated_at) "
                "VALUES ('machine-b', 1, 2000)"
            )
            conn.commit()
        finally:
            conn.close()
        before = snapshot_dir(b_path.parent)

        result = migrate_uuid.migrate_project(PROJECT, write=True)

        assert result.outcome is migrate_uuid.Outcome.REFUSED
        text = "\n".join(migrate_uuid.render(result))
        assert "re-enter" in text
        assert snapshot_dir(b_path.parent) == before


def test_a_joining_machine_holding_a_record_the_dump_lacks_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Equal vectors but different rows can only come from writes outside ccst; the id check is
    the backstop that keeps such a row from being dropped silently."""
    _migrate_first_machine(monkeypatch, tmp_path)
    with on_machine(monkeypatch, tmp_path, "machine-b"):
        b_path = make_format3_db(store.db_path(PROJECT))
        conn = sqlite3.connect(str(b_path))
        try:
            conn.execute(
                "INSERT INTO records (id, record_group, content, created_at, updated_at) "
                "VALUES (3, 'notes', 'stray', 1003, 1003)"
            )
            conn.commit()
        finally:
            conn.close()
        result = migrate_uuid.migrate_project(PROJECT, write=True)
        assert result.outcome is migrate_uuid.Outcome.REFUSED
        assert "1 record(s)" in "\n".join(migrate_uuid.render(result))
        assert formats.database_format(b_path) == formats.PDATA_FORMAT_INTEGER_IDS


def test_a_joining_machine_refuses_a_format4_dump_with_no_recorded_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with on_machine(monkeypatch, tmp_path, "machine-a"):
        conn = repository.connect(PROJECT)
        try:
            dump.write_latest(
                conn, project_root=store.project_root(PROJECT), machine_id="machine-a",
                vector={"machine-a": 1},
            )
        finally:
            conn.close()
    with on_machine(monkeypatch, tmp_path, "machine-b"):
        b_path = make_format3_db(store.db_path(PROJECT))
        result = migrate_uuid.migrate_project(PROJECT, write=True)
        assert result.outcome is migrate_uuid.Outcome.REFUSED
        assert "re-enter" in "\n".join(migrate_uuid.render(result))
        assert formats.database_format(b_path) == formats.PDATA_FORMAT_INTEGER_IDS
