# tests/test_migrate_telemetry.py
from __future__ import annotations

import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from cc_session_tools.cli import migrate_telemetry as mig
from cc_session_tools.lib import telemetry_store
from cccs_hooks.telemetry import TelemetryEntry, log_event


def _generic_line(ts: str, hook: str = "bash-security-review") -> str:
    return json.dumps({
        "v": 1, "ts": ts, "hook": hook, "event": "PreToolUse", "tool": "Bash",
        "session_id": "s1", "cwd": "repos/x", "decision": "allow", "cache": "none",
        "verdict": "safe", "input_hash": "sha256:aa",
    })


def _catchup_line(ts: str, job_id: str) -> str:
    verdict = json.dumps({
        "job_id": job_id, "event": "run", "owed": 1, "ran": 1, "exit_code": 0,
        "duration_ms": 5, "error": None, "consecutive_failures": 0,
    })
    return json.dumps({
        "v": 1, "ts": ts, "hook": "catchup", "event": "", "tool": "", "session_id": "",
        "cwd": "", "decision": "annotate", "cache": "none", "verdict": verdict, "input_hash": "",
    })


def _hook_fire(dest: Path, ts: str, hook: str = "session-tag") -> None:
    """Write a row the way the live hook writer does — i.e. straight into
    telemetry.db, with no involvement from the migration."""
    log_event(
        TelemetryEntry(
            hook=hook, event="SessionStart", tool="", session_id="live",
            cwd_short="repos/x", decision="allow", cache="none",
            verdict="ok", input_hash="",
        ),
        hooks_dir=dest,
    )
    # log_event stamps its own ts; rewrite it so tests can control ordering.
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    conn.execute(
        "UPDATE telemetry_events SET ts = ? WHERE id = (SELECT MAX(id) FROM telemetry_events)",
        (ts,),
    )
    conn.commit()
    conn.close()


def _count(dest: Path, table: str) -> int:
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_migrate_splits_generic_and_catchup_rows(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(
        _generic_line("2026-07-01T00:00:00Z") + "\n" + _catchup_line("2026-07-01T00:00:01Z", "tesco") + "\n"
    )
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)
    assert rc == 0
    assert _count(dest, "telemetry_events") == 1
    assert _count(dest, "catchup_events") == 1


def test_migrate_reads_rotated_slots_oldest_first(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl.2").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")
    (source / "fires.jsonl.1").write_text(_generic_line("2026-06-15T00:00:00Z") + "\n")
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)
    conn = sqlite3.connect(str(dest / "telemetry.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT ts FROM telemetry_events ORDER BY id").fetchall()
    conn.close()
    assert [r["ts"] for r in rows] == [
        "2026-06-01T00:00:00Z", "2026-06-15T00:00:00Z", "2026-07-01T00:00:00Z",
    ]


def test_migrate_skips_malformed_lines_without_failing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text("not json\n" + _generic_line("2026-07-01T00:00:00Z") + "\n")
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)
    assert rc == 0
    assert _count(dest, "telemetry_events") == 1


def test_migrate_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=True)
    assert rc == 0
    assert not (dest / "telemetry.db").exists()
    assert (source / "fires.jsonl").exists()


def test_migrate_backs_up_then_deletes_source_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    (source / "fires.jsonl.1").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)
    assert not (source / "fires.jsonl").exists()
    assert not (source / "fires.jsonl.1").exists()
    backups = list((dest / "migration-backups").glob("fires-jsonl-*.tar.gz"))
    assert len(backups) == 1
    with tarfile.open(backups[0]) as tar:
        names = set(tar.getnames())
    assert names == {"fires.jsonl", "fires.jsonl.1"}


def test_migrate_no_source_files_is_a_no_op(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)
    assert rc == 0
    assert not (dest / "telemetry.db").exists()


# ---------- append into a live store (the real upgrade path) ----------


def test_migrate_appends_into_a_db_the_hooks_already_wrote_to(tmp_path: Path) -> None:
    """The scenario every real upgrade hits: CCST is installed, sessions run
    and fill telemetry.db, and only then does the operator migrate. This used
    to abort with 'already has N row(s) ... re-run with --force'."""
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    _hook_fire(dest, "2026-08-01T00:00:00Z")
    _hook_fire(dest, "2026-08-02T00:00:00Z")
    assert _count(dest, "telemetry_events") == 2

    (source / "fires.jsonl").write_text(
        _generic_line("2026-06-01T00:00:00Z") + "\n" + _generic_line("2026-06-02T00:00:00Z") + "\n"
    )
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)

    assert rc == 0
    # Both the live rows and the imported rows survive — nothing truncated.
    assert _count(dest, "telemetry_events") == 4
    assert not (source / "fires.jsonl").exists()


def test_migrate_into_live_db_keeps_every_original_row_intact(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    _hook_fire(dest, "2026-08-01T00:00:00Z", hook="session-tag")

    (source / "fires.jsonl").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)

    conn = sqlite3.connect(str(dest / "telemetry.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT hook, ts FROM telemetry_events ORDER BY ts").fetchall()
    conn.close()
    assert [(r["hook"], r["ts"]) for r in rows] == [
        ("bash-security-review", "2026-06-01T00:00:00Z"),  # imported
        ("session-tag", "2026-08-01T00:00:00Z"),           # pre-existing live row
    ]


def test_appended_rows_get_higher_ids_than_the_newer_live_rows(tmp_path: Path) -> None:
    """Documents the cost of appending: id order is insertion order, so the
    imported (older) rows sort last by id. Everything that cares about time
    must therefore sort by ts — see the telemetry_query and cursor tests."""
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    _hook_fire(dest, "2026-08-01T00:00:00Z")
    (source / "fires.jsonl").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)

    conn = sqlite3.connect(str(dest / "telemetry.db"))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT ts FROM telemetry_events ORDER BY id").fetchall()
    conn.close()
    assert [r["ts"] for r in rows] == ["2026-08-01T00:00:00Z", "2026-06-01T00:00:00Z"]


# ---------- the marker, not the row count, decides whether to re-run ----------


def test_migrate_records_the_marker_on_success(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)

    conn = sqlite3.connect(str(dest / "telemetry.db"))
    row = conn.execute(
        "SELECT applied_at FROM migrations WHERE name = ?",
        (telemetry_store.LEGACY_JSONL_MIGRATION,),
    ).fetchone()
    conn.close()
    assert row is not None and row[0]


def test_migrate_refuses_a_second_import_once_the_marker_is_set(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    (source / "fires.jsonl").write_text(_generic_line("2026-07-01T00:00:00Z") + "\n")
    mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)

    # A leftover source file reappears (the delete step never reached it).
    (source / "fires.jsonl").write_text(_generic_line("2026-07-02T00:00:00Z") + "\n")
    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)

    assert rc == 1
    assert _count(dest, "telemetry_events") == 1  # second run inserted nothing
    assert (source / "fires.jsonl").exists()      # and deleted nothing


def test_a_live_db_without_the_marker_is_still_migratable(tmp_path: Path) -> None:
    """The bug in one assertion: rows present, marker absent -> import runs.
    The old row-count guard could not tell this from 'already migrated'."""
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    for i in range(5):
        _hook_fire(dest, f"2026-08-0{i + 1}T00:00:00Z")
    (source / "fires.jsonl").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")

    rc = mig.migrate(source_dir=source, dest_dir=dest, dry_run=False)

    assert rc == 0
    assert _count(dest, "telemetry_events") == 6


# ---------- imported catch-up history must not resurface as new activity ----------


def test_migrate_advances_scheduler_cursors_past_imported_catchup_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the cursor bump, `read_since` serves the imported rows to every
    existing session as brand-new catch-up activity: AUTOINCREMENT gives the
    oldest imported rows the highest ids, and the cursor is `WHERE id > ?`."""
    from cc_session_tools.lib.scheduler import cursor, ledger

    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(dest))

    # A session that has already seen everything in the live ledger.
    _hook_fire(dest, "2026-08-01T00:00:00Z")
    cursor.write_cursor("live-session", ledger.current_offset())
    assert ledger.read_since(cursor.read_cursor("live-session"))[0] == []

    (source / "fires.jsonl").write_text(
        _catchup_line("2026-06-01T00:00:00Z", "tesco") + "\n"
        + _catchup_line("2026-06-02T00:00:00Z", "sleep-nudge") + "\n"
    )
    assert mig.migrate(source_dir=source, dest_dir=dest, dry_run=False) == 0

    assert _count(dest, "catchup_events") == 2  # the rows really were imported
    rows, _ = ledger.read_since(cursor.read_cursor("live-session"))
    assert rows == []  # ...but the session is not told about them


def test_migrate_leaves_cursors_alone_when_no_catchup_rows_are_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cc_session_tools.lib.scheduler import cursor

    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(dest))

    cursor.write_cursor("live-session", 4)
    (source / "fires.jsonl").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")
    assert mig.migrate(source_dir=source, dest_dir=dest, dry_run=False) == 0

    assert cursor.read_cursor("live-session") == 4


def test_dry_run_does_not_record_the_marker(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    _hook_fire(dest, "2026-08-01T00:00:00Z")
    (source / "fires.jsonl").write_text(_generic_line("2026-06-01T00:00:00Z") + "\n")

    assert mig.migrate(source_dir=source, dest_dir=dest, dry_run=True) == 0
    assert _count(dest, "telemetry_events") == 1  # nothing appended
    # ...and the real run still works afterwards.
    assert mig.migrate(source_dir=source, dest_dir=dest, dry_run=False) == 0
    assert _count(dest, "telemetry_events") == 2
