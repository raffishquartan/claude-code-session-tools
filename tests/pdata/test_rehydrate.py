from __future__ import annotations

import sqlite3

import pytest

from cc_session_tools.lib.pdata import dump, rehydrate, repository, store, vector_clock_store


def _setup_env(monkeypatch, tmp_path):
    """Point the project-db dir and the project-source-tree root at isolated tmp_path
    subdirectories, matching test_store.py's/test_vector_clock_store.py's fixture conventions."""
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "dbs"))
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))


def _build_local(project: str, *, content: str, vector: dict[str, int]) -> None:
    """Create <project>.db through the real repository.connect() path, with one records row and
    the given pdata_meta vector."""
    conn = repository.connect(project)
    try:
        with repository._immediate(conn):
            repository.insert_base_record(
                conn, record_group="g", content=content, file_path=None,
                created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, vector, updated_at=1)
    finally:
        conn.close()


def _publish_dump(
    project_root, *, remote_project: str, content: str, vector: dict[str, int], machine_id: str,
) -> None:
    """Build a throwaway "remote" project db (its own .db file, under the same PROJECT_DB_DIR)
    and dump it to project_root/.pdata-db-dump/latest.sql — simulating a dump published by
    another machine, without needing two real machines to exercise rehydrate() against."""
    conn = repository.connect(remote_project)
    try:
        with repository._immediate(conn):
            repository.insert_base_record(
                conn, record_group="g", content=content, file_path=None,
                created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, vector, updated_at=1)
        dump.write_latest(conn, project_root=project_root, machine_id=machine_id, vector=vector)
    finally:
        conn.close()


def _local_contents(project: str) -> list[str]:
    conn = repository.connect(project)
    try:
        rows = repository.list_base_records(
            conn, record_group="g", since=None, until=None, limit=None, include_deleted=False,
        )
        return [r["content"] for r in rows]
    finally:
        conn.close()


def test_dump_dominates_fast_forwards_content_and_vector(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _build_local("proj", content="local-content", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="remote-content",
        vector={"ltxy": 1, "mbp": 5}, machine_id="mbp",
    )

    result = rehydrate.rehydrate("proj")

    assert result.outcome is rehydrate.RehydrateOutcome.FAST_FORWARDED
    assert result.from_machine == "mbp"
    assert _local_contents("proj") == ["remote-content"]
    conn = repository.connect("proj")
    try:
        assert vector_clock_store.read_vector(conn) == {"ltxy": 1, "mbp": 5}
    finally:
        conn.close()


@pytest.mark.parametrize(
    "local_vector",
    [
        {"ltxy": 2},  # strictly dominates the dump's {"ltxy": 1}
        {"ltxy": 1},  # exactly equal to the dump's {"ltxy": 1}
    ],
)
def test_local_dominates_or_equal_is_a_noop(monkeypatch, tmp_path, local_vector):
    _setup_env(monkeypatch, tmp_path)
    _build_local("proj", content="local-content", vector=local_vector)
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="remote-content",
        vector={"ltxy": 1}, machine_id="mbp",
    )
    db_path = store.db_path("proj")
    before = db_path.read_bytes()
    before_mtime = db_path.stat().st_mtime_ns

    result = rehydrate.rehydrate("proj")

    assert result.outcome is rehydrate.RehydrateOutcome.NO_OP
    assert db_path.read_bytes() == before
    assert db_path.stat().st_mtime_ns == before_mtime
    assert _local_contents("proj") == ["local-content"]


def test_genuine_fork_is_refused_and_names_the_dumps_machine(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    # Each side has a revision the other lacks: local is ahead on "ltxy", the dump is ahead on
    # "mbp" — neither vector dominates the other.
    _build_local("proj", content="local-content", vector={"ltxy": 2, "mbp": 1})
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="remote-content",
        vector={"ltxy": 1, "mbp": 2}, machine_id="mbp",
    )
    db_path = store.db_path("proj")
    before = db_path.read_bytes()

    result = rehydrate.rehydrate("proj")

    assert result.outcome is rehydrate.RehydrateOutcome.FORK
    assert result.from_machine == "mbp"
    assert db_path.read_bytes() == before
    assert _local_contents("proj") == ["local-content"]


def test_checksum_invalid_is_refused_and_leaves_local_untouched(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _build_local("proj", content="local-content", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="remote-content",
        vector={"ltxy": 1, "mbp": 5}, machine_id="mbp",
    )
    (project_root / ".pdata-db-dump" / "latest.sql").write_text("TRUNCATED")
    db_path = store.db_path("proj")
    before = db_path.read_bytes()

    result = rehydrate.rehydrate("proj")

    assert result.outcome is rehydrate.RehydrateOutcome.CHECKSUM_INVALID
    assert result.from_machine is None
    assert db_path.read_bytes() == before
    assert _local_contents("proj") == ["local-content"]


def test_deferred_when_another_connection_holds_the_write_lock(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _build_local("proj", content="local-content", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="remote-content",
        vector={"ltxy": 1, "mbp": 5}, machine_id="mbp",
    )
    db_path = store.db_path("proj")
    before = db_path.read_bytes()

    holder = sqlite3.connect(db_path, timeout=0)
    holder.execute("BEGIN IMMEDIATE")
    try:
        result = rehydrate.rehydrate("proj")
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert result.outcome is rehydrate.RehydrateOutcome.DEFERRED
    assert result.from_machine is None
    assert db_path.read_bytes() == before
    assert _local_contents("proj") == ["local-content"]
