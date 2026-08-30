from __future__ import annotations

import pytest

from cc_session_tools.lib import machine_identity
from cc_session_tools.lib.pdata import dump, repository, resolve, store, vector_clock_store


def _setup_env(monkeypatch, tmp_path):
    """Point the project-db dir and the project-source-tree root at isolated tmp_path
    subdirectories, and fix this process's machine identity — matches test_rehydrate.py's fixture
    conventions, plus a deterministic machine_id since apply_resolution's vector-clock bookkeeping
    needs one."""
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "dbs"))
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv(machine_identity.MACHINE_NAME_ENV, "ltxy")


def _publish_remote_dump(project_root, *, remote_project, machine_id, vector, build):
    """Builds a throwaway "remote" project db (its own .db file) via `build(conn)` and dumps it
    to project_root/.pdata-db-dump/latest.sql — simulating a dump published by another machine,
    without needing two real machines to exercise resolve.py against (same pattern as
    test_rehydrate.py's `_publish_dump`)."""
    conn = repository.connect(remote_project)
    try:
        with repository._immediate(conn):
            build(conn)
            vector_clock_store.write_vector(conn, vector, updated_at=1)
        dump.write_latest(conn, project_root=project_root, machine_id=machine_id, vector=vector)
    finally:
        conn.close()


def test_diff_pairs_base_and_extension_differences_into_one_record_diff(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="g", content="local-content", file_path=None,
                created_at=1, updated_at=1,
            )
            repository.add_extension_column(conn, "g", "priority", "INTEGER", default=None)
            # ensure_extension_table's backfill already created a bare ext row for record_id —
            # update it, don't insert a second one.
            repository.update_extension_row(conn, "g", record_id, {"priority": 1})
            vector_clock_store.write_vector(conn, {"ltxy": 1, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record_id, "g", "dump-content", None, 1, 2),
        )
        repository.add_extension_column(remote_conn, "g", "priority", "INTEGER", default=None)
        # ensure_extension_table's backfill already created a bare ext row for record_id (the
        # base row above was inserted before this call, in the same group) — update it, don't
        # insert a second one.
        repository.update_extension_row(remote_conn, "g", record_id, {"priority": 2})

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 2}, build=build,
    )

    result = resolve.diff_against_dump("proj")

    assert len(result.records) == 1
    diff = result.records[0]
    assert diff.record_id == record_id
    assert diff.record_group == "g"
    assert diff.local is not None and diff.dump is not None
    assert diff.local["base"]["content"] == "local-content"
    assert diff.dump["base"]["content"] == "dump-content"
    assert diff.local["extension"] == {"priority": 1}
    assert diff.dump["extension"] == {"priority": 2}
    assert diff.is_delete_vs_update is False
    assert diff.id_collision is False
    assert diff.group_mismatch is False


@pytest.mark.parametrize("side", ["dump", "local"])
def test_diff_reports_a_schema_field_present_on_only_one_side(monkeypatch, tmp_path, side):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            repository.insert_base_record(
                conn, record_group="g", content="c", file_path=None, created_at=1, updated_at=1,
            )
            if side == "local":
                repository.add_extension_column(conn, "g", "priority", "INTEGER", default=None)
                repository.upsert_field_description(
                    conn, record_group="g", field_name="priority",
                    description="how urgent", added_at=1,
                )
            vector_clock_store.write_vector(conn, {"ltxy": 1, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        repository.insert_base_record(
            remote_conn, record_group="g", content="c", file_path=None,
            created_at=1, updated_at=1,
        )
        if side == "dump":
            repository.add_extension_column(remote_conn, "g", "priority", "INTEGER", default=None)
            repository.upsert_field_description(
                remote_conn, record_group="g", field_name="priority",
                description="how urgent", added_at=1,
            )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 1}, build=build,
    )

    result = resolve.diff_against_dump("proj")

    assert result.schema_fields == [
        resolve.SchemaFieldDiff(
            record_group="g", field_name="priority",
            present_locally=(side == "local"), present_in_dump=(side == "dump"),
        )
    ]


def test_diff_flags_delete_vs_update_conflict_distinct_from_a_content_diff(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="g", content="c", file_path=None, created_at=1, updated_at=1,
            )
            repository.update_base_record(
                conn, record_id=record_id, expected_version=1, content="updated-locally",
                file_path=None, updated_at=2,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 1, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at, "
            "version, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, "g", "c", None, 1, 3, 2, 3),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 1}, build=build,
    )

    result = resolve.diff_against_dump("proj")

    assert len(result.records) == 1
    diff = result.records[0]
    assert diff.is_delete_vs_update is True
    assert diff.id_collision is False
    assert diff.local["base"]["deleted_at"] is None
    assert diff.dump["base"]["deleted_at"] == 3


def test_diff_flags_id_collision_when_two_independent_new_records_share_an_id(
    monkeypatch, tmp_path,
):
    """records.id has no AUTOINCREMENT — two machines that fork and each insert a brand-new
    record can legitimately allocate the same id to two unrelated rows (see resolve._classify's
    docstring). created_at differing between local's and dump's row for the same id is
    conclusive proof of this, since no write path ever mutates created_at post-insert."""
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="g", content="local-new-record", file_path=None,
                created_at=100, updated_at=100,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 2, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record_id, "g", "dump-new-record", None, 200, 200),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 2}, build=build,
    )

    result = resolve.diff_against_dump("proj")

    assert len(result.records) == 1
    diff = result.records[0]
    assert diff.id_collision is True
    assert diff.is_delete_vs_update is False


def test_apply_resolution_adds_missing_extension_column_before_writing_dump_choice(
    monkeypatch, tmp_path,
):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="g", content="local-content", file_path=None,
                created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 1, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at, "
            "version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record_id, "g", "dump-content", None, 1, 5, 2),
        )
        repository.add_extension_column(remote_conn, "g", "priority", "INTEGER", default=None)
        # ensure_extension_table's backfill already created a bare ext row for record_id — update
        # it, don't insert a second one.
        repository.update_extension_row(remote_conn, "g", record_id, {"priority": 9})

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 2}, build=build,
    )

    resolve.apply_resolution("proj", {record_id: "dump"})

    conn = repository.connect("proj")
    try:
        assert "priority" in repository.list_extension_columns(conn, "g")
        row = repository.get_base_record(conn, record_id)
        assert row["content"] == "dump-content"
        assert row["version"] == 2
        ext_row = repository.get_extension_row(conn, "g", record_id)
        assert ext_row["priority"] == 9
    finally:
        conn.close()


def test_apply_resolution_bumps_local_revision_by_exactly_one_for_any_number_of_records(
    monkeypatch, tmp_path,
):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            id1 = repository.insert_base_record(
                conn, record_group="g", content="local-1", file_path=None,
                created_at=1, updated_at=1,
            )
            id2 = repository.insert_base_record(
                conn, record_group="g", content="local-2", file_path=None,
                created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 3, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (id1, "g", "dump-1", None, 1, 9),
        )
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (id2, "g", "dump-2", None, 1, 9),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 5}, build=build,
    )

    resolve.apply_resolution("proj", {id1: "dump", id2: "local"})

    conn = repository.connect("proj")
    try:
        vector = vector_clock_store.read_vector(conn)
    finally:
        conn.close()
    assert vector["ltxy"] == 4  # bumped by exactly 1 from 3, not by 2 (the record count)
    assert vector["mbp"] == 5  # dump machine's revision adopted


def test_apply_resolution_takes_elementwise_max_for_a_third_machine_known_to_only_one_side(
    monkeypatch, tmp_path,
):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="g", content="local-content", file_path=None,
                created_at=1, updated_at=1,
            )
            # "desktop" is known only locally, at revision 7 — the dump's vector never mentions
            # it at all (not just at a lower value): merge() must still preserve it via its
            # union-of-keys behaviour, not only via the elementwise max of shared keys.
            vector_clock_store.write_vector(
                conn, {"ltxy": 3, "mbp": 1, "desktop": 7}, updated_at=1,
            )
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (record_id, "g", "dump-content", None, 1, 9),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 5}, build=build,
    )

    resolve.apply_resolution("proj", {record_id: "dump"})

    conn = repository.connect("proj")
    try:
        vector = vector_clock_store.read_vector(conn)
    finally:
        conn.close()
    assert vector == {"ltxy": 4, "mbp": 5, "desktop": 7}


def test_apply_resolution_writes_a_fresh_dump_reflecting_the_merged_vector(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="g", content="local-content", file_path=None,
                created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 1, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (record_id, "g", "dump-content", None, 1, 9),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 2}, build=build,
    )

    resolve.apply_resolution("proj", {record_id: "dump"})

    fresh = dump.read_latest(project_root)
    assert fresh.checksum_valid
    assert fresh.machine_id == "ltxy"
    assert fresh.vector == {"ltxy": 2, "mbp": 2}


def test_apply_resolution_rejects_empty_choices_when_the_diff_is_non_empty(monkeypatch, tmp_path):
    """Empty choices are rejected when there's a real record diff to resolve - distinct from the
    schema-only-fork case below, which must report a different, on-topic error instead of this
    generic one (there's no record_id a caller could put in choices for schema-only drift)."""
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            repository.insert_base_record(
                conn, record_group="g", content="local-content", file_path=None,
                created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "g", "dump-content", None, 1, 2),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 2}, build=build,
    )

    with pytest.raises(ValueError, match="at least one"):
        resolve.apply_resolution("proj", {})


def test_apply_resolution_refuses_a_schema_only_fork_instead_of_a_dead_end(monkeypatch, tmp_path):
    """Regression test for a code-review finding: a fork where only record_group_fields differs
    (no record actually differs) has no record_id a caller could put in choices, so the old
    generic "requires at least one record_id" error was an unresolvable dead end - and if a
    resolve.py caller had ALSO resolved a genuine unrelated record diff in the same project, the
    schema drift would have been silently dropped while the published vector claimed the dump
    machine was fully incorporated, the exact "vector lies about full incorporation" shape
    already fixed for partial record resolution. apply_resolution must now refuse with a message
    naming the schema drift specifically, and must never publish over it."""
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            vector_clock_store.write_vector(conn, {"ltxy": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        repository.upsert_field_description(
            remote_conn, record_group="empty-group", field_name="priority",
            description=None, added_at=1,
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 1}, build=build,
    )

    diff = resolve.diff_against_dump("proj")
    assert diff.records == []
    assert len(diff.schema_fields) == 1

    db_path = store.db_path("proj")
    before = db_path.read_bytes()
    with pytest.raises(ValueError, match="record_group_fields"):
        resolve.apply_resolution("proj", {})
    assert db_path.read_bytes() == before  # nothing written, nothing published over the drift


def test_apply_resolution_rejects_an_invalid_choice_value(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="invalid choice"):
        resolve.apply_resolution("proj", {1: "banana"})


def test_apply_resolution_rejects_unknown_record_id(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="g", content="local-content", file_path=None,
                created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 1, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (record_id, "g", "dump-content", None, 1, 9),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 2}, build=build,
    )

    with pytest.raises(ValueError, match="not part of the current diff"):
        resolve.apply_resolution("proj", {999: "dump"})


def test_apply_resolution_refuses_an_id_collision_record(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="g", content="local-new", file_path=None,
                created_at=100, updated_at=100,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 2, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record_id, "g", "dump-new", None, 200, 200),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 2}, build=build,
    )

    with pytest.raises(ValueError, match="id collision"):
        resolve.apply_resolution("proj", {record_id: "dump"})


def test_diff_flags_a_group_rename_as_group_mismatch_not_an_id_collision(monkeypatch, tmp_path):
    """`record_group` is NOT immutable - `ccst pdata rename-group` rewrites it in place
    (rename_group._rename_in_db). A record renamed on one machine only therefore shows the same
    id and the same created_at with different record_groups: a genuine rename, not two unrelated
    rows that happened to be assigned the same id. Only created_at is conclusive for the latter."""
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="renamed", content="c", file_path=None,
                created_at=100, updated_at=100,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 2, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record_id, "original", "c", None, 100, 100),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 2}, build=build,
    )

    result = resolve.diff_against_dump("proj")

    assert len(result.records) == 1
    diff = result.records[0]
    assert diff.group_mismatch is True
    assert diff.id_collision is False
    assert diff.is_delete_vs_update is False


def test_diff_flags_an_id_collision_as_collision_only_even_when_groups_also_differ(
    monkeypatch, tmp_path,
):
    """created_at differing is conclusive proof of an id collision; a differing record_group on
    top of that adds nothing, so the record is reported as a collision and NOT additionally as a
    group mismatch (the two categories are mutually exclusive, collision wins)."""
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="g", content="local-new", file_path=None,
                created_at=100, updated_at=100,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 2, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record_id, "h", "dump-new", None, 200, 200),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 2}, build=build,
    )

    result = resolve.diff_against_dump("proj")

    assert len(result.records) == 1
    diff = result.records[0]
    assert diff.id_collision is True
    assert diff.group_mismatch is False


def test_apply_resolution_refuses_a_group_mismatch_record(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="renamed", content="c", file_path=None,
                created_at=100, updated_at=100,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 2, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (record_id, "original", "c", None, 100, 100),
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 2}, build=build,
    )

    with pytest.raises(ValueError, match="disagree on record_group") as excinfo:
        resolve.apply_resolution("proj", {record_id: "dump"})

    # Distinctly labeled as the group_mismatch case, not the id_collision one (matched above via
    # "disagree on record_group", which is not id_collision's error text). The message must also
    # not tell the caller renaming is unconditionally safe - created_at can coincidentally match
    # for two genuinely unrelated records too (whole-second precision, often mtime-derived), so it
    # must tell the caller to check content/file_path before assuming a rename is the right fix.
    message = str(excinfo.value)
    assert "content" in message and "file_path" in message


def test_apply_resolution_refuses_a_partial_resolve_naming_the_missing_record_ids(
    monkeypatch, tmp_path,
):
    """All-or-nothing: resolving a subset would bump+merge the vector as if the dump machine were
    fully incorporated while leaving the unchosen records unreconciled - the remote's next
    rehydrate would then see DUMP_DOMINATES and wholesale-replace its own real edits."""
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            id1 = repository.insert_base_record(
                conn, record_group="g", content="local-1", file_path=None,
                created_at=1, updated_at=1,
            )
            id2 = repository.insert_base_record(
                conn, record_group="g", content="local-2", file_path=None,
                created_at=1, updated_at=1,
            )
            id3 = repository.insert_base_record(
                conn, record_group="g", content="local-3", file_path=None,
                created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 3, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        for record_id in (id1, id2, id3):
            remote_conn.execute(
                "INSERT INTO records (id, record_group, content, file_path, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (record_id, "g", f"dump-{record_id}", None, 1, 9),
            )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 5}, build=build,
    )

    with pytest.raises(ValueError, match=rf"\[{id2}, {id3}\]"):
        resolve.apply_resolution("proj", {id1: "dump"})

    # Nothing was written: not the row, not the vector, not a fresh dump.
    conn = repository.connect("proj")
    try:
        assert repository.get_base_record(conn, id1)["content"] == "local-1"
        assert vector_clock_store.read_vector(conn) == {"ltxy": 3, "mbp": 1}
    finally:
        conn.close()
    assert dump.read_latest(project_root).vector == {"ltxy": 1, "mbp": 5}


def test_apply_resolution_bumps_the_merged_vector_not_the_pre_merge_local_vector(
    monkeypatch, tmp_path,
):
    """`rehydrate --force` replaces pdata_meta wholesale and can roll local's own counter
    backward, after which a remote dump can hold a HIGHER value for local's own machine than
    local's DB does. Bump-then-merge would then produce a vector merely EQUAL to the dump's, which
    the remote reads as LOCAL_DOMINATES - it never fast-forwards and the resolve is lost. Merging
    first and bumping the merged result always lands strictly above both inputs."""
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            record_id = repository.insert_base_record(
                conn, record_group="g", content="local-content", file_path=None,
                created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 1, "mbp": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (record_id, "g", "dump-content", None, 1, 9),
        )

    # dump_vector["ltxy"] (5) > local_vector["ltxy"] (1): the post-`--force`-rollback shape.
    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 5, "mbp": 2}, build=build,
    )

    resolve.apply_resolution("proj", {record_id: "dump"})

    conn = repository.connect("proj")
    try:
        vector = vector_clock_store.read_vector(conn)
    finally:
        conn.close()
    # merge-then-bump: max(1, 5) = 5, then +1 = 6. Bump-then-merge would give max(1+1, 5) = 5 —
    # exactly the dump's own value, which the remote reads as LOCAL_DOMINATES and never adopts.
    assert vector == {"ltxy": 6, "mbp": 2}
    assert dump.read_latest(project_root).vector == {"ltxy": 6, "mbp": 2}


def test_diff_against_dump_rejects_a_checksum_invalid_dump(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    conn = repository.connect("proj")
    try:
        with repository._immediate(conn):
            repository.insert_base_record(
                conn, record_group="g", content="c", file_path=None, created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, {"ltxy": 1}, updated_at=1)
    finally:
        conn.close()
    project_root = store.project_root("proj")

    def build(remote_conn):
        repository.insert_base_record(
            remote_conn, record_group="g", content="c", file_path=None, created_at=1, updated_at=1,
        )

    _publish_remote_dump(
        project_root, remote_project="proj-remote", machine_id="mbp",
        vector={"ltxy": 1, "mbp": 1}, build=build,
    )
    (project_root / ".pdata-db-dump" / "latest.sql").write_text("TRUNCATED")

    with pytest.raises(ValueError, match="checksum"):
        resolve.diff_against_dump("proj")
