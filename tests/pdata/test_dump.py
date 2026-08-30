from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from cc_session_tools.lib.pdata import dump, repository, store, vector_clock_store


def _build_db(path: Path, *, field_order: list[str], row_order: list[int]) -> sqlite3.Connection:
    """A fresh project .db at `path`, built through repository.connect() so it gets the real
    pdata schema (_BASE_DDL, including pdata_meta) rather than a hand-rolled duplicate.

    repository.connect() only takes a project name, not an arbitrary path — it resolves the
    path itself via store.db_path(project), through CCST_PROJECT_DB_DIR. Pointing that env var
    at `path`'s parent and using `path`'s stem as the project name lands the real connect() flow
    on exactly the path the caller asked for, with no schema duplication.

    field_order/row_order each drive a DIFFERENT sequence of writes for what ends up as
    identical logical content — record_group_fields rows (one per field_order entry, inserted
    through the real upsert_field_description API — the composite-TEXT-PK table whose insertion-
    order-dependence under raw iterdump() is exactly what dump.py's custom ORDER BY fixes) and
    records rows (explicit `id` values from row_order, inserted directly since
    repository.insert_base_record always autoassigns id from the rowid counter and so cannot
    itself land two runs on matching ids in a different order — mirroring the spec's own "same
    three logical rows, reverse insertion order" empirical check for the bare-INTEGER-PK case).
    """
    previous = os.environ.get(store.PROJECT_DB_DIR_ENV)
    os.environ[store.PROJECT_DB_DIR_ENV] = str(path.parent)
    try:
        conn = repository.connect(path.stem)
    finally:
        if previous is None:
            os.environ.pop(store.PROJECT_DB_DIR_ENV, None)
        else:
            os.environ[store.PROJECT_DB_DIR_ENV] = previous

    with repository._immediate(conn):
        for field_name in field_order:
            repository.upsert_field_description(
                conn, record_group="g", field_name=field_name, description=None, added_at=1,
            )
        for row_id in row_order:
            conn.execute(
                "INSERT INTO records (id, record_group, content, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (row_id, "g", f"content-{row_id}", row_id, row_id),
            )
        vector_clock_store.write_vector(conn, {"ltxy": 1}, updated_at=1)
    return conn


def test_dump_is_identical_regardless_of_insertion_or_schema_evolution_order(tmp_path):
    """The exact scenario that broke raw iterdump() during design: two databases holding
    identical logical content, built via a DIFFERENT sequence of operations, must still
    produce byte-identical dumps."""
    con_a = _build_db(tmp_path / "a.db", field_order=["owner", "priority"], row_order=[1, 2, 3])
    con_b = _build_db(tmp_path / "b.db", field_order=["priority", "owner"], row_order=[3, 1, 2])
    assert dump.serialize(con_a) == dump.serialize(con_b)


def test_dump_has_no_pragma_or_file_level_settings(tmp_path):
    con = _build_db(tmp_path / "a.db", field_order=["owner"], row_order=[1])
    text = dump.serialize(con)
    assert "PRAGMA" not in text


def test_write_and_read_latest_roundtrip(tmp_path):
    con = _build_db(tmp_path / "a.db", field_order=["owner"], row_order=[1])
    project_root = tmp_path / "proj"
    dump.write_latest(con, project_root=project_root, machine_id="ltxy", vector={"ltxy": 1})
    result = dump.read_latest(project_root)
    assert result.checksum_valid is True
    assert result.vector == {"ltxy": 1}
    assert result.machine_id == "ltxy"


def test_read_latest_detects_a_corrupted_dump(tmp_path):
    con = _build_db(tmp_path / "a.db", field_order=["owner"], row_order=[1])
    project_root = tmp_path / "proj"
    dump.write_latest(con, project_root=project_root, machine_id="ltxy", vector={"ltxy": 1})
    (project_root / ".pdata-db-dump" / "latest.sql").write_text("TRUNCATED")
    result = dump.read_latest(project_root)
    assert result.checksum_valid is False


def test_archive_keeps_only_24_most_recent(tmp_path):
    con = _build_db(tmp_path / "a.db", field_order=["owner"], row_order=[1])
    project_root = tmp_path / "proj"
    for i in range(30):
        dump.write_latest(con, project_root=project_root, machine_id="ltxy", vector={"ltxy": i})
    archived = list((project_root / ".pdata-db-dump" / "archive").glob("*.sql"))
    assert len(archived) == 24
