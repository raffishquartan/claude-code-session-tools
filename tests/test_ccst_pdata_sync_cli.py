from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import dump, repository, store, vector_clock_store
from cc_session_tools.lib.scheduler import ledger


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path, monkeypatch):
    # In-process setup helpers below (_build_local/_publish_remote_dump, called directly from
    # the test body, not through the subprocess) read these same env vars live via os.environ -
    # monkeypatch.setenv mutates the real process environment, so a plain os.environ.copy()
    # here also carries them into every subprocess _run() call, matching
    # test_ccst_pdata_reconcile_cli.py's base_env pattern.
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "project-db"))
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    monkeypatch.setenv("CCST_MACHINE_NAME", "ltxy")
    # dump/rehydrate now call sync_notify.notify_conflict() on FORK/CHECKSUM_INVALID, which
    # writes a real ledger row and attempts a real Telegram send unless isolated - without this,
    # every conflict test in this file would write into the developer's actual
    # ~/.local/share/claude/telemetry.db and could send real Telegram messages from their real
    # credentials. This is the same incident test_sync_notify.py's own autouse fixture documents
    # (see its comment); this file needs the identical isolation since it now exercises the same
    # notify_conflict() call path through the real CLI subprocess.
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("CCCS_CREDS_PATH", str(tmp_path / "no-creds-here"))
    # `ccst pdata sync-check` runs the spec's occupancy gate, which shells out to
    # `pgrep -x claude` and fails safe to True when it can't enumerate processes. This suite is
    # routinely run from inside a live `claude` session, and a subprocess cannot be
    # monkeypatched, so PATH is pointed at a stub `pgrep` that reports no matches - the same
    # determinism tests/pdata/test_pdata_sync_hook.py gets from its `unoccupied` fixture.
    # _stub_pgrep(..., pids=...) overrides it for the one test that wants an occupied project.
    _stub_pgrep(tmp_path, monkeypatch)
    return os.environ.copy()


def _stub_pgrep(tmp_path: Path, monkeypatch, *, pids: tuple[int, ...] = ()) -> None:
    """Put a fake `pgrep` first on PATH. With no pids it exits 1 with no output, exactly as real
    pgrep does when nothing matches, so `occupancy.is_occupied` returns False."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "pgrep"
    body = "".join(f"echo {pid}\n" for pid in pids) or ""
    script.write_text(f"#!/bin/sh\n{body}exit {0 if pids else 1}\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _build_local(project: str, *, content: str = "local", vector: dict[str, int] | None = None) -> None:
    """Create <project>.db through the real repository.connect() path, with one records row and
    (optionally) a pdata_meta vector - matches test_rehydrate.py's/test_resolve.py's fixture
    conventions."""
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


def _publish_remote_dump(
    project_root: Path, *, remote_project: str, content: str, vector: dict[str, int],
    machine_id: str,
) -> None:
    """Build a throwaway "remote" project db and dump it to project_root/.pdata-db-dump/latest.sql
    - simulating a dump published by another machine, without needing two real machines
    (same pattern as test_rehydrate.py's/test_resolve.py's _publish_remote_dump).

    The throwaway remote project's own .db is written into a sibling directory, not the
    project-db dir base_env points --project/--all-projects subprocess runs at: if it lived
    there too, `verify.discover_projects()` (which `--all-projects` uses) would pick up
    "<name>-remote" as a real project of its own and pollute every --all-projects assertion in
    this file with an extra, unrelated project count."""
    local_db_dir = os.environ[store.PROJECT_DB_DIR_ENV]
    remote_db_dir = str(Path(local_db_dir).parent / "remote-project-db")
    os.environ[store.PROJECT_DB_DIR_ENV] = remote_db_dir
    try:
        conn = repository.connect(remote_project)
        try:
            with repository._immediate(conn):
                repository.insert_base_record(
                    conn, record_group="g", content=content, file_path=None,
                    created_at=1, updated_at=1,
                )
                vector_clock_store.write_vector(conn, vector, updated_at=1)
            dump.write_latest(
                conn, project_root=project_root, machine_id=machine_id, vector=vector,
            )
        finally:
            conn.close()
    finally:
        os.environ[store.PROJECT_DB_DIR_ENV] = local_db_dir


# ---------------------------------------------------------------- pdata dump ----


def test_dump_requires_project_or_all_projects(base_env):
    r = _run(base_env, "pdata", "dump")
    assert r.returncode == 2


def test_dump_rejects_both_project_and_all_projects(base_env):
    r = _run(base_env, "pdata", "dump", "--project", "x", "--all-projects")
    assert r.returncode == 2


def test_dump_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "dump", "--project", "../escape")
    assert r.returncode == 2


def test_dump_happy_path_first_ever_dump(base_env):
    _build_local("proj", vector={"ltxy": 1})

    r = _run(base_env, "pdata", "dump", "--project", "proj")

    assert r.returncode == 0, r.stderr
    assert "published" in r.stdout
    info = dump.read_latest(store.project_root("proj"))
    assert info.checksum_valid
    assert info.vector == {"ltxy": 1}
    assert info.machine_id == "ltxy"


def test_dump_refuses_on_fork_without_force(base_env):
    _build_local("proj", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "dump", "--project", "proj")

    assert r.returncode == 1
    assert "ccst pdata resolve --project proj" in r.stderr
    # nothing was overwritten - the dump on disk is still the remote one.
    info = dump.read_latest(project_root)
    assert info.machine_id == "mbp"
    # notify_conflict() fired for real through the CLI, not just in sync_notify's own unit
    # tests - the SessionStart digest channel (ledger.record) is reachable from production code.
    rows = ledger.read_recent(job_id="pdata-sync:proj")
    assert len(rows) == 1
    assert rows[0]["event"] == "run"
    assert "fork" in str(rows[0]["error"])


def test_dump_refuses_when_dump_dominates_without_force(base_env):
    # Local has never bumped its own vector (empty) - the published dump is strictly ahead,
    # which is what `ccst pdata rehydrate` is for, not a local `dump` publish.
    _build_local("proj")
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "dump", "--project", "proj")

    assert r.returncode == 1
    assert "ccst pdata resolve --project proj" in r.stderr


def test_dump_force_overwrites_a_fork(base_env):
    _build_local("proj", content="local-wins", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "dump", "--project", "proj", "--force")

    assert r.returncode == 0, r.stderr
    info = dump.read_latest(project_root)
    assert info.checksum_valid
    assert info.machine_id == "ltxy"
    # a plain dump republishes the local vector unchanged - it is not itself a local write.
    assert info.vector == {"ltxy": 1}


def test_dump_all_projects_compact_summary(base_env):
    _build_local("clean-proj", vector={"ltxy": 1})
    _build_local("forked-proj", vector={"ltxy": 1})
    _publish_remote_dump(
        store.project_root("forked-proj"), remote_project="forked-proj-remote",
        content="remote", vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "dump", "--all-projects")

    assert r.returncode == 1
    assert "published 1, refused 1" in r.stdout


def test_dump_all_projects_force(base_env):
    _build_local("clean-proj", vector={"ltxy": 1})
    _build_local("forked-proj", vector={"ltxy": 1})
    _publish_remote_dump(
        store.project_root("forked-proj"), remote_project="forked-proj-remote",
        content="remote", vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "dump", "--all-projects", "--force")

    assert r.returncode == 0, r.stdout
    assert "published 2 of 2" in r.stdout


# ---------------------------------------------------------------- pdata rehydrate ----


def test_rehydrate_requires_project_or_all_projects(base_env):
    r = _run(base_env, "pdata", "rehydrate")
    assert r.returncode == 2


def test_rehydrate_rejects_both_project_and_all_projects(base_env):
    r = _run(base_env, "pdata", "rehydrate", "--project", "x", "--all-projects")
    assert r.returncode == 2


def test_rehydrate_happy_path_fast_forwards(base_env):
    _build_local("proj")
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="from-remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "rehydrate", "--project", "proj")

    assert r.returncode == 0, r.stderr
    assert "fast-forwarded from mbp" in r.stdout
    conn = repository.connect("proj")
    try:
        rows = repository.list_base_records(
            conn, record_group="g", since=None, until=None, limit=None, include_deleted=False,
        )
        assert [row["content"] for row in rows] == ["from-remote"]
    finally:
        conn.close()


def test_rehydrate_no_op_when_local_already_ahead(base_env):
    _build_local("proj", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="stale",
        vector={}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "rehydrate", "--project", "proj")

    assert r.returncode == 0, r.stderr
    assert "already up to date" in r.stdout


def test_rehydrate_conflict_prints_resolve_message(base_env):
    _build_local("proj", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "rehydrate", "--project", "proj")

    assert r.returncode == 1
    assert "ccst pdata resolve --project proj" in r.stderr
    assert "mbp" in r.stderr


def test_rehydrate_force_overrides_a_fork(base_env):
    _build_local("proj", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "rehydrate", "--project", "proj", "--force")

    assert r.returncode == 0, r.stderr
    assert "fast-forwarded from mbp" in r.stdout


def test_rehydrate_checksum_invalid_prints_dump_force_message(base_env):
    _build_local("proj")  # never dumped - read_latest reports checksum_valid=False

    r = _run(base_env, "pdata", "rehydrate", "--project", "proj")

    assert r.returncode == 1
    assert "ccst pdata dump --force" in r.stderr


def test_rehydrate_deferred_is_not_treated_as_an_error(base_env):
    _build_local("proj")
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    db_path = store.db_path("proj")
    holder = None
    try:
        import sqlite3

        holder = sqlite3.connect(db_path, timeout=0)
        holder.execute("BEGIN IMMEDIATE")

        r = _run(base_env, "pdata", "rehydrate", "--project", "proj")

        assert r.returncode == 0, r.stderr
        assert "retry later" in r.stdout
        assert "ccst pdata resolve" not in r.stdout
        assert "ccst pdata resolve" not in r.stderr
    finally:
        if holder is not None:
            holder.execute("ROLLBACK")
            holder.close()


def test_rehydrate_all_projects_compact_summary(base_env):
    _build_local("ff-proj")
    _publish_remote_dump(
        store.project_root("ff-proj"), remote_project="ff-proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    _build_local("fork-proj", vector={"ltxy": 1})
    _publish_remote_dump(
        store.project_root("fork-proj"), remote_project="fork-proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "rehydrate", "--all-projects")

    assert r.returncode == 1
    assert "fast-forwarded 1" in r.stdout
    assert "fork 1" in r.stdout


# ---------------------------------------------------------------- pdata resolve ----


def test_resolve_requires_project_or_all_projects(base_env):
    r = _run(base_env, "pdata", "resolve")
    assert r.returncode == 2


def test_resolve_rejects_both_project_and_all_projects(base_env):
    r = _run(base_env, "pdata", "resolve", "--project", "x", "--all-projects")
    assert r.returncode == 2


def test_resolve_has_no_force_flag(base_env):
    # Deliberate: apply_resolution is already all-or-nothing/explicit, there is no
    # partial/unsafe default behaviour for --force to override.
    r = _run(base_env, "pdata", "resolve", "--project", "x", "--force")
    assert r.returncode == 2
    assert "unrecognized arguments" in r.stderr


def test_resolve_diagnostic_mode_clean_when_no_diff(base_env):
    _build_local("proj", vector={"ltxy": 1})
    dump_pub = _run(base_env, "pdata", "dump", "--project", "proj")
    assert dump_pub.returncode == 0, dump_pub.stderr

    r = _run(base_env, "pdata", "resolve", "--project", "proj")

    assert r.returncode == 0, r.stdout
    assert "clean" in r.stdout


def test_resolve_diagnostic_mode_reports_outstanding_records_and_schema_fields(base_env):
    _build_local("proj", content="local-content", vector={"ltxy": 1})
    project_root = store.project_root("proj")

    def build(remote_conn):
        remote_conn.execute(
            "INSERT INTO records (id, record_group, content, file_path, created_at, "
            "updated_at) VALUES (999, 'g', 'dump-content', NULL, 5, 5)"
        )
        repository.add_extension_column(remote_conn, "g", "priority", "INTEGER", default=None)
        # _diff_schema_fields reads record_group_fields, not the ext table's own columns - a
        # field is only "registered" for schema-diff purposes once it has a catalog row.
        repository.upsert_field_description(
            remote_conn, record_group="g", field_name="priority",
            description="d", added_at=5,
        )

    conn = repository.connect("proj-remote")
    try:
        with repository._immediate(conn):
            build(conn)
            vector_clock_store.write_vector(conn, {"mbp": 1}, updated_at=1)
        dump.write_latest(conn, project_root=project_root, machine_id="mbp", vector={"mbp": 1})
    finally:
        conn.close()

    r = _run(base_env, "pdata", "resolve", "--project", "proj")

    assert r.returncode == 1, r.stdout
    assert "2 record(s) + 1 schema field(s) to resolve" in r.stdout
    assert "local-only" in r.stdout  # the real local record, absent from the fake remote dump
    assert "dump-only" in r.stdout  # record 999, present only in the dump
    assert "schema field g.priority: dump only" in r.stdout


def test_resolve_diagnostic_reports_checksum_invalid_error(base_env):
    _build_local("proj")  # never dumped

    r = _run(base_env, "pdata", "resolve", "--project", "proj")

    assert r.returncode == 1
    assert "ccst pdata dump --force" in r.stderr


def test_resolve_diagnostic_all_projects_compact_summary(base_env):
    _build_local("clean-proj", vector={"ltxy": 1})
    dump_pub = _run(base_env, "pdata", "dump", "--project", "clean-proj")
    assert dump_pub.returncode == 0, dump_pub.stderr

    _build_local("diff-proj", content="local", vector={"ltxy": 1})
    _publish_remote_dump(
        store.project_root("diff-proj"), remote_project="diff-proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "resolve", "--all-projects")

    assert r.returncode == 1
    assert "clean-proj: clean" in r.stdout
    assert "record(s)" in r.stdout and "schema field(s) outstanding" in r.stdout


def test_resolve_choice_rejects_all_projects(base_env):
    r = _run(
        base_env, "pdata", "resolve", "--all-projects", "--choice", "1=local",
    )
    assert r.returncode == 2
    assert "--choice requires --project" in r.stderr


def test_resolve_choice_rejects_malformed_shape(base_env):
    r = _run(
        base_env, "pdata", "resolve", "--project", "proj", "--choice", "notanumber=local",
    )
    assert r.returncode == 2
    assert "malformed --choice" in r.stderr


def test_resolve_choice_apply_happy_path(base_env):
    _build_local("proj", content="local-content", vector={"ltxy": 1})
    project_root = store.project_root("proj")

    conn = repository.connect("proj")
    try:
        record_id = repository.list_base_records(
            conn, record_group="g", since=None, until=None, limit=None, include_deleted=False,
        )[0]["id"]
    finally:
        conn.close()

    remote_conn = repository.connect("proj-remote")
    try:
        with repository._immediate(remote_conn):
            remote_conn.execute(
                "INSERT INTO records (id, record_group, content, file_path, created_at, "
                "updated_at) VALUES (?, 'g', 'dump-content', NULL, 1, 2)",
                (record_id,),
            )
            vector_clock_store.write_vector(remote_conn, {"mbp": 1}, updated_at=1)
        dump.write_latest(
            remote_conn, project_root=project_root, machine_id="mbp", vector={"mbp": 1},
        )
    finally:
        remote_conn.close()

    r = _run(
        base_env, "pdata", "resolve", "--project", "proj",
        "--choice", f"{record_id}=dump",
    )

    assert r.returncode == 0, r.stderr
    assert "resolved 1 record(s)" in r.stdout

    conn = repository.connect("proj")
    try:
        rows = repository.list_base_records(
            conn, record_group="g", since=None, until=None, limit=None, include_deleted=False,
        )
        assert [row["content"] for row in rows] == ["dump-content"]
    finally:
        conn.close()


def test_resolve_choice_apply_prints_apply_resolution_error_verbatim(base_env):
    _build_local("proj", content="local-content", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(
        base_env, "pdata", "resolve", "--project", "proj",
        "--choice", "999999=local",
    )

    assert r.returncode == 1
    assert "999999" in r.stderr
    assert "are not part of the current diff" in r.stderr


# ---------------------------------------------------------------- pdata sync-check ----


def test_sync_check_requires_project_or_all_projects(base_env):
    r = _run(base_env, "pdata", "sync-check")
    assert r.returncode == 2


def test_sync_check_rejects_both_project_and_all_projects(base_env):
    r = _run(base_env, "pdata", "sync-check", "--project", "x", "--all-projects")
    assert r.returncode == 2


def test_sync_check_has_no_force_flag(base_env):
    """Deliberate: this is the automatic, repeating trigger. Overriding a fork or a checksum
    failure is a human decision made with `ccst pdata dump/rehydrate --force`."""
    r = _run(base_env, "pdata", "sync-check", "--project", "x", "--force")
    assert r.returncode == 2
    assert "unrecognized arguments" in r.stderr


def test_sync_check_no_projects_found(base_env):
    r = _run(base_env, "pdata", "sync-check", "--all-projects")
    assert r.returncode == 2
    assert "no project databases found" in r.stderr


def test_sync_check_fast_forwards_when_the_dump_is_ahead(base_env):
    _build_local("proj")
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="from-remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    before = (project_root / ".pdata-db-dump" / "latest.sql").read_text()

    r = _run(base_env, "pdata", "sync-check", "--project", "proj")

    assert r.returncode == 0, r.stderr
    assert "fast-forwarded from mbp" in r.stdout
    conn = repository.connect("proj")
    try:
        rows = repository.list_base_records(
            conn, record_group="g", since=None, until=None, limit=None, include_deleted=False,
        )
        assert [row["content"] for row in rows] == ["from-remote"]
    finally:
        conn.close()
    # A fast-forward is terminal - no dump-check, so latest.sql is untouched.
    assert (project_root / ".pdata-db-dump" / "latest.sql").read_text() == before


def test_sync_check_publishes_when_local_is_ahead(base_env):
    _build_local("proj", vector={"ltxy": 2})
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="stale",
        vector={"ltxy": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "sync-check", "--project", "proj")

    assert r.returncode == 0, r.stderr
    assert "published (machine_id=ltxy)" in r.stdout
    info = dump.read_latest(project_root)
    assert info.machine_id == "ltxy"
    assert info.vector == {"ltxy": 2}


def test_sync_check_skips_an_unchanged_republish(base_env):
    """The reason this subcommand exists rather than reusing `ccst pdata dump --all-projects`:
    an hourly job must not rewrite latest.sql for every idle project, every hour, forever."""
    _build_local("proj", vector={"ltxy": 1})
    first = _run(base_env, "pdata", "sync-check", "--project", "proj")
    assert first.returncode == 0, first.stderr
    assert "published" in first.stdout
    latest = store.project_root("proj") / ".pdata-db-dump" / "latest.sql"
    before_text = latest.read_text()
    before_mtime = latest.stat().st_mtime_ns
    time.sleep(0.01)

    r = _run(base_env, "pdata", "sync-check", "--project", "proj")

    assert r.returncode == 0, r.stderr
    assert "unchanged - nothing new to publish" in r.stdout
    assert latest.read_text() == before_text
    assert latest.stat().st_mtime_ns == before_mtime


def test_sync_check_reports_a_fork_as_a_conflict(base_env):
    _build_local("proj", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(base_env, "pdata", "sync-check", "--project", "proj")

    assert r.returncode == 1
    assert "ccst pdata resolve --project proj" in r.stderr
    # nothing was overwritten - the dump on disk is still the remote one.
    assert dump.read_latest(project_root).machine_id == "mbp"
    rows = ledger.read_recent(job_id="pdata-sync:proj")
    assert len(rows) == 1
    assert "fork" in str(rows[0]["error"])


def test_sync_check_reports_a_corrupt_dump_as_a_conflict(base_env):
    _build_local("proj", vector={"ltxy": 1})
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    latest = project_root / ".pdata-db-dump" / "latest.sql"
    latest.write_text(latest.read_text() + "\n-- tampered\n")

    r = _run(base_env, "pdata", "sync-check", "--project", "proj")

    assert r.returncode == 1
    assert "ccst pdata dump --force" in r.stderr


def test_sync_check_publishes_a_first_dump_instead_of_calling_it_corrupt(base_env):
    """A project that has never been dumped is the normal state right after `ccst pdata init`.
    It must publish, not report a checksum conflict every hour forever."""
    _build_local("proj", vector={"ltxy": 1})
    assert not (store.project_root("proj") / ".pdata-db-dump" / "latest.sql").exists()

    r = _run(base_env, "pdata", "sync-check", "--project", "proj")

    assert r.returncode == 0, r.stderr
    assert "published" in r.stdout
    assert ledger.read_recent(job_id="pdata-sync:proj") == []


def test_sync_check_defers_without_calling_it_a_conflict(base_env):
    """DEFERRED proves the dump dominates local, so falling through to the dump-check would
    re-derive DUMP_DOMINATES and push a false "conflict" for transient lock contention."""
    _build_local("proj")
    project_root = store.project_root("proj")
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    import sqlite3

    holder = sqlite3.connect(store.db_path("proj"), timeout=0)
    try:
        holder.execute("BEGIN IMMEDIATE")

        r = _run(base_env, "pdata", "sync-check", "--project", "proj")

        assert r.returncode == 0, r.stderr
        assert "retry later" in r.stdout
        assert "ccst pdata resolve" not in r.stdout + r.stderr
        assert ledger.read_recent(job_id="pdata-sync:proj") == []
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_sync_check_skips_a_project_a_live_session_is_working_in(base_env, tmp_path, monkeypatch):
    """Spec "Process safety": the hourly job never swaps a project's .db out from under a live
    session. Driven with a real child process whose cwd is the project root, plus the stub pgrep
    reporting it - so the whole occupancy path runs for real, deterministically."""
    _build_local("proj")
    project_root = store.project_root("proj")
    project_root.mkdir(parents=True, exist_ok=True)
    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="from-remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    occupant = subprocess.Popen(["sleep", "30"], cwd=str(project_root))
    try:
        _stub_pgrep(tmp_path, monkeypatch, pids=(occupant.pid,))
        env = os.environ.copy()

        r = _run(env, "pdata", "sync-check", "--project", "proj")

        assert r.returncode == 0, r.stderr
        assert "a live session is working in this project - skipped" in r.stdout
        # local was NOT swapped, and nothing was published over the remote dump either.
        conn = repository.connect("proj")
        try:
            rows = repository.list_base_records(
                conn, record_group="g", since=None, until=None, limit=None,
                include_deleted=False,
            )
            assert [row["content"] for row in rows] == ["local"]
        finally:
            conn.close()
        assert dump.read_latest(project_root).machine_id == "mbp"
    finally:
        occupant.terminate()
        occupant.wait(timeout=10)


def test_sync_check_all_projects_compact_summary(base_env):
    _build_local("ff-proj")
    _publish_remote_dump(
        store.project_root("ff-proj"), remote_project="ff-proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    _build_local("fork-proj", vector={"ltxy": 1})
    _publish_remote_dump(
        store.project_root("fork-proj"), remote_project="fork-proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )
    _build_local("new-proj", vector={"ltxy": 1})

    r = _run(base_env, "pdata", "sync-check", "--all-projects")

    assert r.returncode == 1
    assert "fast-forwarded 1" in r.stdout
    assert "published 1" in r.stdout
    assert "conflict 1" in r.stdout
    assert "of 3 project(s)" in r.stdout
    # compact mode prints no per-project detail lines
    assert "ff-proj:" not in r.stdout


def test_sync_check_all_projects_records_an_error_and_continues(base_env, tmp_path):
    """One unreadable project must not abort the whole unattended batch - the
    _cmd_pdata_dump/_cmd_pdata_rehydrate established shape."""
    _build_local("good-proj", vector={"ltxy": 1})
    # A .db that is not a SQLite database at all: discover_projects() finds it, opening it fails.
    (tmp_path / "project-db" / "broken-proj.db").write_text("not a database")

    r = _run(base_env, "pdata", "sync-check", "--all-projects")

    assert r.returncode == 2
    assert "published 1" in r.stdout
    assert "errors 1" in r.stdout
    assert "of 2 project(s)" in r.stdout


def test_resolve_choice_apply_rejects_invalid_choice_value(base_env):
    _build_local("proj", content="local-content", vector={"ltxy": 1})
    project_root = store.project_root("proj")

    conn = repository.connect("proj")
    try:
        record_id = repository.list_base_records(
            conn, record_group="g", since=None, until=None, limit=None, include_deleted=False,
        )[0]["id"]
    finally:
        conn.close()

    _publish_remote_dump(
        project_root, remote_project="proj-remote", content="remote",
        vector={"mbp": 1}, machine_id="mbp",
    )

    r = _run(
        base_env, "pdata", "resolve", "--project", "proj",
        "--choice", f"{record_id}=maybe",
    )

    assert r.returncode == 1
    assert "invalid choice 'maybe'" in r.stderr
