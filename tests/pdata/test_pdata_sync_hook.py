"""Tests for the SessionStart/SessionEnd pdata-sync hook (`cccs_hooks.pdata_sync`).

Isolation, non-negotiable in this file: every test runs through the autouse `_isolate` fixture
below because *every* conflict path here reaches `sync_notify.notify_conflict()`, which writes a
real `catchup_events` ledger row and attempts a real Telegram send. Two real incidents already
happened on this branch from tests that skipped this (real rows in the developer's actual
~/.local/share/claude/telemetry.db, real Telegram messages from their real bot) - see
tests/pdata/test_sync_notify.py's own fixture comment and tests/test_ccst_pdata_sync_cli.py's
`base_env`. Autouse rather than opt-in so a new test cannot forget it.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cc_session_tools.lib import occupancy
from cc_session_tools.lib.pdata import (
    dump,
    repository,
    rehydrate,
    store,
    sync_notify,
    vector_clock_store,
)
from cccs_hooks import pdata_sync

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


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Capture notify_conflict calls at the pdata_sync boundary, so a test asserts on what the
    hook decided to report rather than on sync_notify's own already-tested internals."""
    calls: list[tuple[str, str, str]] = []

    def fake(project: str, *, outcome: str, detail: str) -> None:
        calls.append((project, outcome, detail))

    monkeypatch.setattr(pdata_sync.sync_notify, "notify_conflict", fake)
    return calls


@pytest.fixture
def unoccupied(monkeypatch: pytest.MonkeyPatch) -> None:
    """`occupancy.is_occupied` shells out to `pgrep -x claude` and fails safe to True when it
    can't; both make it nondeterministic under test. Every test that isn't specifically about
    the occupancy gate pins it."""
    monkeypatch.setattr(pdata_sync.occupancy, "is_occupied", lambda root, *, exclude_pid=None: False)


def _cwd_for(project: str) -> str:
    return str(store.project_root(project))


def _build_local(project: str, *, content: str, vector: dict[str, int]) -> None:
    """Create <project>.db through the real repository.connect() path - matches
    test_rehydrate.py's fixture convention."""
    store.project_root(project).mkdir(parents=True, exist_ok=True)
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
    project: str, *, content: str, vector: dict[str, int], machine_id: str,
) -> None:
    """Publish a dump into <project_root>/.pdata-db-dump/latest.sql from a throwaway "remote"
    project db, simulating another machine - same approach as test_rehydrate.py."""
    conn = repository.connect(f"{project}-remote")
    try:
        with repository._immediate(conn):
            repository.insert_base_record(
                conn, record_group="g", content=content, file_path=None,
                created_at=1, updated_at=1,
            )
            vector_clock_store.write_vector(conn, vector, updated_at=1)
        dump.write_latest(
            conn, project_root=store.project_root(project), machine_id=machine_id, vector=vector,
        )
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


# ---------- project derivation from cwd ----------


def test_session_start_no_ops_when_cwd_is_not_under_the_projects_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notified: list[tuple[str, str, str]],
) -> None:
    """This hook fires on every session start regardless of project, so a cwd that was never
    going to be a pdata project must cost nothing - not even an occupancy check."""
    def explode(*args: object, **kwargs: object) -> bool:
        raise AssertionError("occupancy must not be checked for a non-pdata cwd")

    monkeypatch.setattr(pdata_sync.occupancy, "is_occupied", explode)
    elsewhere = tmp_path / "somewhere" / "else"
    elsewhere.mkdir(parents=True)

    assert pdata_sync.on_session_start(str(elsewhere), session_pid=1234) is None
    assert notified == []


def test_session_start_no_ops_for_a_project_that_was_never_pdata_migrated(
    monkeypatch: pytest.MonkeyPatch, notified: list[tuple[str, str, str]],
) -> None:
    """Under ~/cc but with no <project>.db - `ccst pdata init` was never run here."""
    def explode(*args: object, **kwargs: object) -> bool:
        raise AssertionError("occupancy must not be checked for an unmigrated project")

    monkeypatch.setattr(pdata_sync.occupancy, "is_occupied", explode)
    root = store.project_root("notmigrated")
    root.mkdir(parents=True)

    assert pdata_sync.on_session_start(_cwd_for("notmigrated"), session_pid=1234) is None
    assert notified == []


def test_session_end_no_ops_for_a_project_that_was_never_pdata_migrated(
    notified: list[tuple[str, str, str]],
) -> None:
    root = store.project_root("notmigrated")
    root.mkdir(parents=True)

    pdata_sync.on_session_end(_cwd_for("notmigrated"))

    assert notified == []
    assert not (root / ".pdata-db-dump").exists()


# ---------- SessionStart: occupancy gate ----------


def test_session_start_skips_rehydrate_entirely_when_the_project_is_occupied(
    monkeypatch: pytest.MonkeyPatch, notified: list[tuple[str, str, str]],
) -> None:
    """Another live session in this project takes priority - don't change its data mid-task. The
    skip is still reported: silence here used to be indistinguishable from "the occupancy check
    itself is broken", which is exactly the failure mode Chris hit."""
    _build_local("proj", content="local-content", vector={MACHINE: 1})
    _publish_dump("proj", content="remote-content", vector={MACHINE: 1, "mbp": 5}, machine_id="mbp")
    monkeypatch.setattr(pdata_sync.occupancy, "is_occupied", lambda root, *, exclude_pid=None: True)

    message = pdata_sync.on_session_start(_cwd_for("proj"), session_pid=1234)

    assert message is not None
    assert "proj" in message
    assert "already open in another Claude Code session" in message
    assert _local_contents("proj") == ["local-content"]  # not rehydrated
    assert notified == []


def test_session_start_excludes_its_own_launching_process_from_the_occupancy_check(
    monkeypatch: pytest.MonkeyPatch, unoccupied: None,
) -> None:
    """Spec "Process safety": by the time this hook fires, its own `claude` process already
    exists and would otherwise always make the project look occupied by itself."""
    _build_local("proj", content="local-content", vector={MACHINE: 1})
    seen: list[int | None] = []

    def spy(root: Path, *, exclude_pid: int | None = None) -> bool:
        seen.append(exclude_pid)
        return False

    monkeypatch.setattr(pdata_sync.occupancy, "is_occupied", spy)
    pdata_sync.on_session_start(_cwd_for("proj"), session_pid=4321)

    assert seen == [4321]


# ---------- SessionStart: rehydrate outcomes ----------


def test_session_start_fast_forward_rehydrates_and_names_machine_and_timestamp(
    unoccupied: None, notified: list[tuple[str, str, str]],
) -> None:
    _build_local("proj", content="local-content", vector={MACHINE: 1})
    _publish_dump("proj", content="remote-content", vector={MACHINE: 1, "mbp": 5}, machine_id="mbp")

    message = pdata_sync.on_session_start(_cwd_for("proj"), session_pid=1234)

    assert _local_contents("proj") == ["remote-content"]
    assert message is not None
    info = dump.read_latest(store.project_root("proj"))
    assert info.dumped_at is not None
    assert message == (
        "Re-hydrating project pdata DB based on updates made on `mbp` at "
        f"`{dump.format_dumped_at(info.dumped_at)}`"
    )
    assert notified == []


def test_session_start_fast_forward_falls_back_to_mtime_when_dumped_at_is_absent(
    unoccupied: None, notified: list[tuple[str, str, str]],
) -> None:
    """A dump written before the dumped_at field existed - simulated here by hand-stripping the
    header line and recomputing the checksum dump.write_latest() itself would have produced, the
    same technique test_read_latest_does_not_mistake_record_content_for_header_metadata uses for
    header-boundary edge cases. Message must fall back to latest.sql's own mtime, matching
    init_service.py's _format_published_at sibling formatter for the identical case, not the
    unhelpful "an unknown time" this hook used to print here."""
    import hashlib

    _build_local("proj", content="local-content", vector={MACHINE: 1})
    _publish_dump("proj", content="remote-content", vector={MACHINE: 1, "mbp": 5}, machine_id="mbp")
    latest = store.project_root("proj") / ".pdata-db-dump" / "latest.sql"
    stripped = "\n".join(
        line for line in latest.read_text().splitlines() if not line.startswith("-- dumped_at=")
    ) + "\n"
    latest.write_text(stripped)
    (latest.parent / "latest.sha256").write_text(hashlib.sha256(stripped.encode()).hexdigest())

    message = pdata_sync.on_session_start(_cwd_for("proj"), session_pid=1234)

    assert _local_contents("proj") == ["remote-content"]
    assert message is not None
    expected_when = dump.format_dumped_at(int(latest.stat().st_mtime))
    assert message == (
        f"Re-hydrating project pdata DB based on updates made on `mbp` at `{expected_when}`"
    )
    assert notified == []


def test_session_start_no_op_outcome_still_reports_that_the_hook_ran(
    unoccupied: None, notified: list[tuple[str, str, str]],
) -> None:
    """The common case, on every single session start - still gets a visible line, so the hook
    having run is never in question (see the occupied-gate test's comment)."""
    _build_local("proj", content="local-content", vector={MACHINE: 2})
    _publish_dump("proj", content="remote-content", vector={MACHINE: 1}, machine_id="mbp")

    message = pdata_sync.on_session_start(_cwd_for("proj"), session_pid=1234)

    assert message is not None
    assert "proj" in message
    assert notified == []


def test_session_start_deferred_outcome_still_reports_that_the_hook_ran(
    monkeypatch: pytest.MonkeyPatch, unoccupied: None, notified: list[tuple[str, str, str]],
) -> None:
    """DEFERRED is transient (another writer holds the lock right now), not a conflict - the
    next trigger retries, and nothing goes to sync_notify - but it's still visible to this
    session, not silently indistinguishable from NO_OP."""
    _build_local("proj", content="local-content", vector={MACHINE: 1})
    monkeypatch.setattr(
        pdata_sync.rehydrate, "rehydrate",
        lambda project, **kwargs: rehydrate.RehydrateResult(
            outcome=rehydrate.RehydrateOutcome.DEFERRED
        ),
    )

    message = pdata_sync.on_session_start(_cwd_for("proj"), session_pid=1234)

    assert message is not None
    assert "proj" in message
    assert notified == []


def test_session_start_fork_notifies_and_also_tells_the_live_session_immediately(
    unoccupied: None, notified: list[tuple[str, str, str]],
) -> None:
    """A live session that just hit a fork must not have to wait for a future digest to learn
    about its own session's conflict - it gets both channels."""
    _build_local("proj", content="local-content", vector={MACHINE: 2, "mbp": 1})
    _publish_dump("proj", content="remote-content", vector={MACHINE: 1, "mbp": 2}, machine_id="mbp")

    message = pdata_sync.on_session_start(_cwd_for("proj"), session_pid=1234)

    assert _local_contents("proj") == ["local-content"]  # nothing written on a fork
    assert len(notified) == 1
    project, outcome, detail = notified[0]
    assert (project, outcome) == ("proj", "fork")
    assert "ccst pdata resolve --project proj" in detail
    assert message is not None
    assert detail in message
    assert "proj" in message


def test_session_start_checksum_invalid_notifies_and_tells_the_session(
    unoccupied: None, notified: list[tuple[str, str, str]],
) -> None:
    _build_local("proj", content="local-content", vector={MACHINE: 1})
    _publish_dump("proj", content="remote-content", vector={MACHINE: 1, "mbp": 5}, machine_id="mbp")
    latest = store.project_root("proj") / ".pdata-db-dump" / "latest.sql"
    latest.write_text(latest.read_text() + "\n-- corrupted after checksumming\n")

    message = pdata_sync.on_session_start(_cwd_for("proj"), session_pid=1234)

    assert len(notified) == 1
    project, outcome, detail = notified[0]
    assert (project, outcome) == ("proj", "checksum_invalid")
    assert "ccst pdata dump --force" in detail
    assert message is not None
    assert detail in message


def test_session_start_conflict_detail_is_the_same_text_the_cli_reports(
    unoccupied: None, notified: list[tuple[str, str, str]],
) -> None:
    """One source of truth for the guidance text: the hook and `ccst pdata rehydrate` must not
    drift into two differently-worded descriptions of the same conflict."""
    _build_local("proj", content="local-content", vector={MACHINE: 2, "mbp": 1})
    _publish_dump("proj", content="remote-content", vector={MACHINE: 1, "mbp": 2}, machine_id="mbp")

    pdata_sync.on_session_start(_cwd_for("proj"), session_pid=1234)

    expected = rehydrate.conflict_detail(
        rehydrate.RehydrateResult(
            outcome=rehydrate.RehydrateOutcome.FORK, from_machine="mbp",
        ),
        project="proj",
    )
    assert notified[0][2] == expected


# ---------- SessionEnd: publish decision ----------


def test_session_end_publishes_a_dump_when_local_has_new_writes(
    notified: list[tuple[str, str, str]],
) -> None:
    _build_local("proj", content="local-content", vector={MACHINE: 3})
    _publish_dump("proj", content="older", vector={MACHINE: 1}, machine_id=MACHINE)

    pdata_sync.on_session_end(_cwd_for("proj"))

    info = dump.read_latest(store.project_root("proj"))
    assert info.checksum_valid
    assert info.machine_id == MACHINE
    assert info.vector == {MACHINE: 3}
    assert notified == []


def test_session_end_publishes_the_very_first_dump_when_none_exists(
    notified: list[tuple[str, str, str]],
) -> None:
    _build_local("proj", content="local-content", vector={MACHINE: 1})

    pdata_sync.on_session_end(_cwd_for("proj"))

    info = dump.read_latest(store.project_root("proj"))
    assert info.checksum_valid
    assert info.vector == {MACHINE: 1}
    assert notified == []


def test_session_end_does_no_occupancy_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per the spec's Triggers table, SessionEnd's dump direction needs no gate - dumping is a
    read-only copy SQLite already makes safe against concurrent access."""
    def explode(*args: object, **kwargs: object) -> bool:
        raise AssertionError("SessionEnd must not gate on occupancy")

    monkeypatch.setattr(pdata_sync.occupancy, "is_occupied", explode)
    _build_local("proj", content="local-content", vector={MACHINE: 1})

    pdata_sync.on_session_end(_cwd_for("proj"))

    assert dump.read_latest(store.project_root("proj")).checksum_valid


def test_session_end_refuses_to_publish_over_a_fork_and_notifies(
    notified: list[tuple[str, str, str]],
) -> None:
    """The only way the user learns about a conflict found at session end: there is no live
    session left to print a systemMessage to."""
    _build_local("proj", content="local-content", vector={MACHINE: 2, "mbp": 1})
    _publish_dump("proj", content="remote", vector={MACHINE: 1, "mbp": 2}, machine_id="mbp")
    before = (store.project_root("proj") / ".pdata-db-dump" / "latest.sql").read_text()

    pdata_sync.on_session_end(_cwd_for("proj"))

    assert (store.project_root("proj") / ".pdata-db-dump" / "latest.sql").read_text() == before
    assert len(notified) == 1
    project, outcome, detail = notified[0]
    assert (project, outcome) == ("proj", "fork")
    assert detail == dump.refusal_detail("proj")


def test_session_end_refuses_to_publish_over_a_dominating_dump_and_notifies(
    notified: list[tuple[str, str, str]],
) -> None:
    """DUMP_DOMINATES too, not only FORK: a plain dump publish is not itself a local write, so
    overwriting a dump that is ahead of us would silently discard revisions we never took."""
    _build_local("proj", content="local-content", vector={MACHINE: 1})
    _publish_dump("proj", content="remote", vector={MACHINE: 1, "mbp": 2}, machine_id="mbp")

    pdata_sync.on_session_end(_cwd_for("proj"))

    assert notified == [("proj", "dump_dominates", dump.refusal_detail("proj"))]


# ---------- main() wiring ----------


def _run_main(monkeypatch: pytest.MonkeyPatch, payload: object) -> tuple[int, dict[str, object]]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = pdata_sync.main()
    return rc, json.loads(out.getvalue())


def test_main_routes_session_start_and_emits_the_message(
    monkeypatch: pytest.MonkeyPatch, unoccupied: None, notified: list[tuple[str, str, str]],
) -> None:
    _build_local("proj", content="local-content", vector={MACHINE: 1})
    _publish_dump("proj", content="remote-content", vector={MACHINE: 1, "mbp": 5}, machine_id="mbp")

    rc, emitted = _run_main(monkeypatch, {
        "hook_event_name": "SessionStart", "cwd": _cwd_for("proj"), "session_id": "s1",
    })

    assert rc == 0
    hook_output = emitted["hookSpecificOutput"]
    assert isinstance(hook_output, dict)
    assert hook_output["hookEventName"] == "SessionStart"
    assert "Re-hydrating project pdata DB" in str(emitted["systemMessage"])
    assert _local_contents("proj") == ["remote-content"]


def test_main_resolves_the_launching_claude_pid_rather_than_using_raw_getppid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: `main()` used to pass bare `os.getppid()` as the occupancy exclusion,
    which is an `sh -c` wrapper's PID rather than the real `claude` process's whenever `/bin/sh`
    is dash - see `occupancy.launching_claude_pid`'s docstring. `main()` must resolve through it
    instead, and pass whatever it returns straight through to `is_occupied`."""
    _build_local("proj", content="local-content", vector={MACHINE: 1})
    seen_start_pid: list[int] = []
    seen_exclude: list[int | None] = []

    def fake_launching_claude_pid(start_pid: int, **kwargs: object) -> int | None:
        seen_start_pid.append(start_pid)
        return 9999

    def fake_is_occupied(root: Path, *, exclude_pid: int | None = None) -> bool:
        seen_exclude.append(exclude_pid)
        return False

    monkeypatch.setattr(pdata_sync.occupancy, "launching_claude_pid", fake_launching_claude_pid)
    monkeypatch.setattr(pdata_sync.occupancy, "is_occupied", fake_is_occupied)
    monkeypatch.setattr(pdata_sync.os, "getppid", lambda: 4242)

    _run_main(monkeypatch, {
        "hook_event_name": "SessionStart", "cwd": _cwd_for("proj"), "session_id": "s1",
    })

    assert seen_start_pid == [4242]
    assert seen_exclude == [9999]


def test_main_routes_session_end_and_stays_silent(
    monkeypatch: pytest.MonkeyPatch, notified: list[tuple[str, str, str]],
) -> None:
    _build_local("proj", content="local-content", vector={MACHINE: 1})

    rc, emitted = _run_main(monkeypatch, {
        "hook_event_name": "SessionEnd", "cwd": _cwd_for("proj"),
        "session_id": "s1", "reason": "clear",
    })

    assert rc == 0
    assert "systemMessage" not in emitted
    # SessionEnd has no `hookSpecificOutput` shape in Claude Code's output schema at all - any
    # such payload, whatever its hookEventName, fails validation and surfaces as a hook-failure
    # error (this was a real bug: the hook used to emit one unconditionally for every event).
    assert "hookSpecificOutput" not in emitted
    assert dump.read_latest(store.project_root("proj")).checksum_valid


def test_main_session_end_emits_no_hook_specific_output_even_when_the_real_work_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _build_local("proj", content="local-content", vector={MACHINE: 1})

    def boom(cwd: str) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(pdata_sync, "on_session_end", boom)

    rc, emitted = _run_main(monkeypatch, {
        "hook_event_name": "SessionEnd", "cwd": _cwd_for("proj"), "session_id": "s1",
    })

    assert rc == 0
    assert "hookSpecificOutput" not in emitted
    assert "systemMessage" not in emitted


def test_main_never_blocks_a_session_on_unparseable_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rc, emitted = _run_main(monkeypatch, "not json at all")
    assert rc == 0
    assert "hookSpecificOutput" in emitted


def test_main_never_blocks_a_session_when_the_real_work_raises(
    monkeypatch: pytest.MonkeyPatch, unoccupied: None,
) -> None:
    _build_local("proj", content="local-content", vector={MACHINE: 1})

    def boom(project: str, **kwargs: object) -> rehydrate.RehydrateResult:
        raise OSError("disk went away")

    monkeypatch.setattr(pdata_sync.rehydrate, "rehydrate", boom)

    rc, emitted = _run_main(monkeypatch, {
        "hook_event_name": "SessionStart", "cwd": _cwd_for("proj"), "session_id": "s1",
    })

    assert rc == 0
    assert "systemMessage" not in emitted


def test_main_ignores_an_event_it_is_not_registered_for(
    monkeypatch: pytest.MonkeyPatch, unoccupied: None,
) -> None:
    _build_local("proj", content="local-content", vector={MACHINE: 1})

    rc, emitted = _run_main(monkeypatch, {
        "hook_event_name": "UserPromptSubmit", "cwd": _cwd_for("proj"), "session_id": "s1",
    })

    assert rc == 0
    assert "systemMessage" not in emitted
    assert not (store.project_root("proj") / ".pdata-db-dump").exists()


# ---------- RehydrateResult.dumped_at ----------


def test_rehydrate_result_carries_the_dumps_own_embedded_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not latest.sql's filesystem mtime - that reflects when *this* machine's copy last changed
    on disk (OneDrive sync-settle time), not when the source machine published it."""
    _build_local("proj", content="local-content", vector={MACHINE: 1})
    _publish_dump("proj", content="remote-content", vector={MACHINE: 1, "mbp": 5}, machine_id="mbp")
    published_at = dump.read_latest(store.project_root("proj")).dumped_at

    result = rehydrate.rehydrate("proj")

    assert result.outcome is rehydrate.RehydrateOutcome.FAST_FORWARDED
    assert result.dumped_at == published_at


def test_rehydrate_result_dumped_at_is_none_for_non_fast_forward_outcomes() -> None:
    _build_local("proj", content="local-content", vector={MACHINE: 2, "mbp": 1})
    _publish_dump("proj", content="remote", vector={MACHINE: 1, "mbp": 2}, machine_id="mbp")

    result = rehydrate.rehydrate("proj")

    assert result.outcome is rehydrate.RehydrateOutcome.FORK
    assert result.dumped_at is None


# ---------- shared helpers extracted for this hook ----------


def test_conflict_detail_rejects_a_non_conflict_outcome() -> None:
    """The two callers only ever ask about FORK/CHECKSUM_INVALID; anything else is a caller bug
    that must surface, not silently produce a misleading message."""
    with pytest.raises(ValueError, match="no_op"):
        rehydrate.conflict_detail(
            rehydrate.RehydrateResult(outcome=rehydrate.RehydrateOutcome.NO_OP),
            project="proj",
        )


def test_sync_notify_wrote_no_real_rows(tmp_path: Path) -> None:
    """Guard for this file's own isolation: `notified` stubs notify_conflict out entirely, so
    even the isolated ledger must stay empty. If this ever fails, a code path is reaching the
    real notification channels."""
    from cc_session_tools.lib.scheduler import ledger

    assert ledger.read_recent(job_id="pdata-sync:proj") == []
    assert sync_notify._JOB_ID_PREFIX == "pdata-sync:"
