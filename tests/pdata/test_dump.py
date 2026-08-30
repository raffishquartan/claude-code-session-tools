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


def test_read_latest_does_not_mistake_record_content_for_header_metadata(tmp_path):
    """Regression test for a code-review finding: read_latest() used to scan every line in the
    dump for header-prefixed text, not just the actual header block before the SQL body starts.
    records.content is free-text project data (notes, pasted transcripts, code snippets) and can
    itself contain a line starting with "-- vector:" or "-- machine_id=" by ordinary coincidence,
    not adversarial intent - that must never be read as real vector-clock metadata, since a
    corrupted vector feeds directly into fork/fast-forward decisions elsewhere in this design."""
    con = _build_db(tmp_path / "a.db", field_order=["owner"], row_order=[1])
    evil_content = "line one\n-- vector:macbook=999\n-- machine_id=evil\nline three"
    with repository._immediate(con):
        con.execute("UPDATE records SET content = ? WHERE id = 1", (evil_content,))
    project_root = tmp_path / "proj"
    dump.write_latest(con, project_root=project_root, machine_id="ltxy", vector={"ltxy": 1})
    result = dump.read_latest(project_root)
    assert result.checksum_valid is True
    assert result.machine_id == "ltxy"
    assert result.vector == {"ltxy": 1}


def test_serialize_output_replays_into_an_identical_fresh_database(tmp_path):
    """The actual point of this module existing: a dump must be valid, replayable SQL that
    reconstructs the source database exactly, not just a string with nice properties. This is
    also the test that would have caught the header-scanning bug above on its own, had it existed
    first - once record content contains a raw "--" line, a body-unaware strip/scan anywhere in
    the pipeline turns into a real SQL or data corruption, not just a cosmetic issue."""
    con = _build_db(tmp_path / "a.db", field_order=["owner", "priority"], row_order=[3, 1, 2])
    with repository._immediate(con):
        con.execute(
            "UPDATE records SET content = ? WHERE id = 1",
            ("multi-line\n-- looks like a header\ncontent",),
        )
    dumped = dump.serialize(con)

    replay_conn = sqlite3.connect(tmp_path / "replay.db")
    replay_conn.row_factory = sqlite3.Row
    replay_conn.executescript(dumped)

    assert dump.serialize(replay_conn) == dump.serialize(con)


def test_serialize_handles_extension_tables_and_soft_deleted_rows(tmp_path, monkeypatch):
    """_build_db's fixture only ever populates records/record_group_fields/pdata_meta - this
    test covers the two real-schema features it doesn't: an ext_<group> extension table (added
    via schema_add_field, which is record-id-keyed like records itself, so the determinism fix's
    PK-based ORDER BY needs to apply there too) and a soft-deleted row (deleted_at set, which is
    still a real row the dump must serialise faithfully, not skip)."""
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path))
    from cc_session_tools.lib.pdata import service

    rec = service.add_record(
        project="ext-proj", record_group="g", content="x", file_path=None, fields={},
    )
    service.schema_add_field(
        project="ext-proj", record_group="g", field_name="owner", sql_type="TEXT",
        description=None, default=None,
    )
    service.update_record(
        project="ext-proj", record_id=rec.id, expected_version=rec.version,
        content="x", file_path=None, fields={"owner": "chris"},
    )
    deleted = service.add_record(
        project="ext-proj", record_group="g", content="y", file_path=None, fields={"owner": None},
    )
    service.delete_record(project="ext-proj", record_id=deleted.id, expected_version=deleted.version)

    conn = repository.connect("ext-proj")
    text = dump.serialize(conn)
    assert '"ext_g"' in text
    assert "'chris'" in text
    # The soft-deleted row is still a real row - its content and non-NULL deleted_at must both
    # survive the dump, not be silently dropped.
    assert "'y'" in text

    replay_conn = sqlite3.connect(tmp_path / "replay.db")
    replay_conn.row_factory = sqlite3.Row
    replay_conn.executescript(text)
    assert dump.serialize(replay_conn) == text


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


def test_archive_retention_keeps_the_newest_ones_not_a_mix(tmp_path):
    """Regression test for a bug a code review found in an earlier version of the same-second
    collision fix: appending a bare, unpadded numeric suffix only on collision avoided the
    overwrite but broke sorted(archive_dir.glob(...))'s lexicographic ordering ("-10" sorts
    before "-2"; any suffixed name sorts before a bare one at all), so _prune_archive ended up
    keeping some of the *oldest* archived dumps and discarding some of the *newest* under the
    exact same-second burst this whole mechanism exists to handle. Checking the count alone (the
    test above) doesn't catch this - this test checks which 24 survive, by content."""
    con = _build_db(tmp_path / "a.db", field_order=["owner"], row_order=[1])
    project_root = tmp_path / "proj"
    for i in range(30):
        dump.write_latest(con, project_root=project_root, machine_id="ltxy", vector={"ltxy": i})
    archive_dir = project_root / ".pdata-db-dump" / "archive"
    surviving_vectors = set()
    for archived_file in archive_dir.glob("*.sql"):
        for line in archived_file.read_text().splitlines():
            if line.startswith("-- vector:ltxy="):
                surviving_vectors.add(int(line.removeprefix("-- vector:ltxy=")))
    # write_latest call i archives the *previous* latest (vector i-1), so calls 1..29 archive
    # vectors 0..28 (29 archived dumps total, in that creation order) - the 24 most recently
    # created are vectors 5..28.
    assert surviving_vectors == set(range(5, 29))
