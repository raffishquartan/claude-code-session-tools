"""Tests for `lib/pdata/sync_check.py` - the hourly `ccsched` job's per-project algorithm.

Isolation, non-negotiable in this file, for the same reason
`tests/pdata/test_pdata_sync_hook.py` states: every conflict path here reaches
`sync_notify.notify_conflict()`, which writes a real `catchup_events` ledger row and attempts a
real Telegram send. Three real incidents have already happened on this branch from tests that
skipped this. Both fixtures below are autouse so a new test cannot forget either one.

The second autouse fixture pins `occupancy.is_occupied`: it shells out to `pgrep -x claude` and
fails safe to True when it can't, and this suite is routinely run from inside a live `claude`
session, so leaving it real would make every test here nondeterministic.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import (
    dump,
    repository,
    store,
    sync_check,
    vector_clock_store,
)

MACHINE = "ltxy"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_MACHINE_NAME", MACHINE)
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("CCCS_CREDS_PATH", str(tmp_path / "no-creds-here"))


@pytest.fixture(autouse=True)
def unoccupied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sync_check.occupancy, "is_occupied", lambda root, *, exclude_pid=None: False
    )


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Capture notify_conflict calls at the sync_check boundary, so a test asserts on what
    sync_check decided to report rather than on sync_notify's already-tested internals."""
    calls: list[tuple[str, str, str]] = []

    def fake(project: str, *, outcome: str, detail: str) -> None:
        calls.append((project, outcome, detail))

    monkeypatch.setattr(sync_check.sync_notify, "notify_conflict", fake)
    return calls


def _build_local(project: str, *, content: str = "local", vector: dict[str, int] | None = None) -> None:
    conn = repository.connect(project)
    try:
        with repository._immediate(conn):
            repository.insert_base_record(
                conn, record_group="g", content=content, file_path=None,
                created_at=1, updated_at=1,
            )
            if vector is not None:
                vector_clock_store.write_vector(conn, vector, updated_at=1)
    finally:
        conn.close()


def _publish_dump(
    project_root: Path, *, remote_project: str, content: str, vector: dict[str, int],
    machine_id: str,
) -> None:
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


# ------------------------------------------------------------------ rehydrate half ----


def test_dump_dominates_fast_forwards_and_skips_the_dump_check(notified):
    _build_local("proj", content="local", vector={"mbp": 1})
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="from-remote",
        vector={"mbp": 3}, machine_id="mbp",
    )
    before = (project_root / ".pdata-db-dump" / "latest.sql").read_text()

    result = sync_check.check_project("proj")

    assert result.outcome is sync_check.SyncOutcome.FAST_FORWARDED
    assert result.from_machine == "mbp"
    assert notified == []
    # A fast-forward makes local's vector exactly equal to the published one, so a dump-check
    # here could only ever re-discover "nothing new" - it must not rewrite latest.sql.
    assert (project_root / ".pdata-db-dump" / "latest.sql").read_text() == before


def test_fork_is_reported_as_a_conflict_and_never_dump_checked(notified):
    _build_local("proj", content="local", vector={MACHINE: 1})
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    before = (project_root / ".pdata-db-dump" / "latest.sql").read_text()

    result = sync_check.check_project("proj")

    assert result.outcome is sync_check.SyncOutcome.CONFLICT
    assert result.conflict_outcome == "fork"
    assert "ccst pdata resolve --project proj" in (result.detail or "")
    assert [c[0] for c in notified] == ["proj"]
    assert notified[0][1] == "fork"
    assert (project_root / ".pdata-db-dump" / "latest.sql").read_text() == before


def test_a_corrupt_published_dump_is_reported_as_a_conflict(notified):
    _build_local("proj", vector={MACHINE: 1})
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    # Corrupt the published dump so its recorded sha256 no longer matches.
    latest = project_root / ".pdata-db-dump" / "latest.sql"
    latest.write_text(latest.read_text() + "\n-- tampered\n")

    result = sync_check.check_project("proj")

    assert result.outcome is sync_check.SyncOutcome.CONFLICT
    assert result.conflict_outcome == "checksum_invalid"
    assert "ccst pdata dump --force" in (result.detail or "")
    assert notified[0][1] == "checksum_invalid"


def test_an_occupied_project_with_a_corrupt_dump_is_skipped_not_reported(monkeypatch, notified):
    """Regression test for a code-review finding: the occupancy check must run before the
    checksum-validity check, not after, matching the spec's own ordering (Triggers table,
    SessionStart row: "First, the... occupancy check... Otherwise: [checksum/fork evaluation]")
    and Task 12's on_session_start(), which the hourly row explicitly says to follow "exactly".
    An earlier version of check_project() checked checksum validity first, so a project that was
    both occupied by a live session AND sitting behind a corrupted published dump got reported
    as a conflict (Telegram + digest) every hourly cycle instead of silently skipping like any
    other occupied project."""
    _build_local("proj", vector={MACHINE: 1})
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    latest = project_root / ".pdata-db-dump" / "latest.sql"
    latest.write_text(latest.read_text() + "\n-- tampered\n")
    monkeypatch.setattr(
        sync_check.occupancy, "is_occupied", lambda root, *, exclude_pid=None: True
    )

    result = sync_check.check_project("proj")

    assert result.outcome is sync_check.SyncOutcome.OCCUPIED
    assert notified == []


def test_deferred_is_terminal_and_never_reported_as_a_conflict(notified, tmp_path):
    """rehydrate() only returns DEFERRED after compare() already came back DUMP_DOMINATES, so
    falling through to the dump-check would make decide_publish() re-derive that same
    DUMP_DOMINATES and push a "conflict" notification for what is ordinary, transient lock
    contention. DEFERRED must stop the cycle for this project instead."""
    import sqlite3

    _build_local("proj")
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    holder = sqlite3.connect(store.db_path("proj"), timeout=0)
    try:
        holder.execute("BEGIN IMMEDIATE")

        result = sync_check.check_project("proj")

        assert result.outcome is sync_check.SyncOutcome.DEFERRED
        assert notified == []
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_an_occupied_project_is_skipped_entirely(monkeypatch, notified):
    """Spec "Process safety": the hourly job, like SessionStart, must not swap a project's .db
    out from under a live session. The dump-check is skipped too - without the rehydrate-check
    having established the local-vs-dump relationship, decide_publish() could legitimately come
    back DUMP_DOMINATES and be mis-reported as a conflict for as long as the session stays open."""
    _build_local("proj")
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    monkeypatch.setattr(
        sync_check.occupancy, "is_occupied", lambda root, *, exclude_pid=None: True
    )
    before = (project_root / ".pdata-db-dump" / "latest.sql").read_text()

    result = sync_check.check_project("proj")

    assert result.outcome is sync_check.SyncOutcome.OCCUPIED
    assert notified == []
    # local was not swapped either
    conn = repository.connect("proj")
    try:
        rows = repository.list_base_records(
            conn, record_group="g", since=None, until=None, limit=None, include_deleted=False,
        )
        assert [r["content"] for r in rows] == ["local"]
    finally:
        conn.close()
    assert (project_root / ".pdata-db-dump" / "latest.sql").read_text() == before


def test_the_occupancy_gate_is_checked_against_the_project_root(monkeypatch):
    seen: list[Path] = []

    def spy(root: Path, *, exclude_pid: int | None = None) -> bool:
        seen.append(root)
        return False

    _build_local("proj", vector={MACHINE: 1})
    _publish_dump(
        store.project_root("proj"), remote_project="proj-remote", content="remote",
        vector={MACHINE: 1}, machine_id="mbp",
    )
    monkeypatch.setattr(sync_check.occupancy, "is_occupied", spy)

    sync_check.check_project("proj")

    assert seen == [store.project_root("proj")]


# ------------------------------------------------------------------ dump half ----


def test_local_ahead_publishes(notified):
    _build_local("proj", content="local", vector={MACHINE: 2})
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="stale",
        vector={MACHINE: 1}, machine_id="mbp",
    )

    result = sync_check.check_project("proj")

    assert result.outcome is sync_check.SyncOutcome.PUBLISHED
    assert result.machine_id == MACHINE
    assert notified == []
    info = dump.read_latest(project_root)
    assert info.checksum_valid
    assert info.machine_id == MACHINE
    assert info.vector == {MACHINE: 2}


def test_an_unchanged_project_is_not_republished(notified):
    """The whole reason dump.is_no_op_publish() was added: an automatic, repeating trigger must
    not rewrite latest.sql/latest.sha256 (and archive a copy, and push a OneDrive sync) every
    hour for a project nobody has touched."""
    _build_local("proj", content="local", vector={MACHINE: 1})
    project_root = store.project_root("proj")
    conn = repository.connect("proj")
    try:
        dump.write_latest(
            conn, project_root=project_root, machine_id=MACHINE, vector={MACHINE: 1},
        )
    finally:
        conn.close()
    latest = project_root / ".pdata-db-dump" / "latest.sql"
    before_text = latest.read_text()
    before_mtime = latest.stat().st_mtime_ns
    archive_before = len(list((project_root / ".pdata-db-dump" / "archive").glob("*.sql")))

    result = sync_check.check_project("proj")

    assert result.outcome is sync_check.SyncOutcome.UNCHANGED
    assert notified == []
    assert latest.read_text() == before_text
    assert latest.stat().st_mtime_ns == before_mtime
    assert len(list((project_root / ".pdata-db-dump" / "archive").glob("*.sql"))) == archive_before


def test_a_project_with_no_published_dump_yet_publishes_its_first_dump(notified):
    """The normal state of every project between `ccst pdata init` and its first publish. It
    must NOT reach the rehydrate half's CHECKSUM_INVALID branch - that would push a "published
    dump fails its checksum check" notification every single hour, forever, for a project whose
    only problem is that nobody has dumped it yet."""
    _build_local("proj", content="local", vector={MACHINE: 1})
    project_root = store.project_root("proj")
    assert not (project_root / ".pdata-db-dump" / "latest.sql").exists()

    result = sync_check.check_project("proj")

    assert result.outcome is sync_check.SyncOutcome.PUBLISHED
    assert notified == []
    info = dump.read_latest(project_root)
    assert info.checksum_valid
    assert info.machine_id == MACHINE
    assert info.vector == {MACHINE: 1}


def test_the_occupancy_gate_is_not_consulted_when_nothing_is_published_yet(monkeypatch):
    """The gate exists only to protect the content-replacing rehydrate direction (spec: "Process
    safety" - "Dumping needs no gate"). With no dump to rehydrate from there is nothing to gate,
    and `is_occupied` shells out to pgrep, so it must not be paid here."""
    calls: list[Path] = []
    _build_local("proj", vector={MACHINE: 1})
    monkeypatch.setattr(
        sync_check.occupancy, "is_occupied",
        lambda root, *, exclude_pid=None: calls.append(root) or False,
    )

    sync_check.check_project("proj")

    assert calls == []


def test_a_dump_check_refusal_is_reported_as_a_conflict(monkeypatch, notified):
    """decide_publish() refusing after a NO_OP rehydrate means the published dump changed
    between rehydrate's read and ours (a OneDrive delivery mid-cycle). Rare, but real - and a
    genuine conflict, unlike the DEFERRED/OCCUPIED cases above."""
    from cc_session_tools.lib.pdata import vector_clock

    _build_local("proj", content="local", vector={MACHINE: 2})
    project_root = store.project_root("proj")
    _publish_dump(
        project_root, remote_project="proj-remote", content="stale",
        vector={MACHINE: 1}, machine_id="mbp",
    )
    monkeypatch.setattr(
        sync_check.dump, "decide_publish",
        lambda *, local_vector, existing: vector_clock.Comparison.FORK,
    )
    before = (project_root / ".pdata-db-dump" / "latest.sql").read_text()

    result = sync_check.check_project("proj")

    assert result.outcome is sync_check.SyncOutcome.CONFLICT
    assert result.conflict_outcome == "fork"
    assert "refusing to publish" in (result.detail or "")
    assert notified[0][1] == "fork"
    assert (project_root / ".pdata-db-dump" / "latest.sql").read_text() == before


def test_check_project_rejects_a_bad_project_name():
    with pytest.raises(ValueError):
        sync_check.check_project("../escape")
