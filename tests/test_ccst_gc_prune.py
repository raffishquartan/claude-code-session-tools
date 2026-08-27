"""Tests for ccst gc prune and cc_session_tools.lib.session_gc's prune()/format_prune_report()."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

import pytest

from cc_session_tools.lib import db as _db
from cc_session_tools.lib.session_gc import (
    PruneReport,
    StorePruneResult,
    _uuid_age_hours,
    build_report,
    format_prune_report,
    format_report,
    prune,
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


ORPHAN_UUID = "22222222-2222-2222-2222-222222222222"
YOUNG_UUID = "33333333-3333-3333-3333-333333333333"
UNKNOWN_UUID = "44444444-4444-4444-4444-444444444444"
OTHER_UUID = "55555555-5555-5555-5555-555555555555"

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
OLD_TS = "2026-01-01T00:00:00Z"  # far older than any reasonable --min-age-hours


def _recent_ts() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FMT)


# Same DDL as lib/scheduler/store.py, lib/messaging/repository.py, lib/sessions_db.py —
# duplicated here rather than imported, matching test_ccst_gc_report.py's own convention.
_SCHEDULER_DDL = """
CREATE TABLE IF NOT EXISTS cursors (
    session_uuid TEXT PRIMARY KEY,
    offset       INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reconcile_throttle (
    session_uuid       TEXT PRIMARY KEY,
    last_reconciled_at TEXT NOT NULL
);
"""
_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS cursors (
    session_uuid          TEXT NOT NULL,
    partition             TEXT NOT NULL,
    high_water_message_id TEXT NOT NULL,
    PRIMARY KEY (session_uuid, partition)
);
"""
_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS session_tags (
    uuid       TEXT PRIMARY KEY,
    tag        TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _seed_reconcile(scheduler_dir: Path, uuid: str, ts: str) -> None:
    conn = _db.connect(scheduler_dir / "ccsched.db", ddl=_SCHEDULER_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO reconcile_throttle (session_uuid, last_reconciled_at) VALUES (?, ?)",
        (uuid, ts),
    )
    conn.commit()
    conn.close()


def _seed_scheduler_cursor(scheduler_dir: Path, uuid: str) -> None:
    conn = _db.connect(scheduler_dir / "ccsched.db", ddl=_SCHEDULER_DDL)
    conn.execute("INSERT OR REPLACE INTO cursors (session_uuid, offset) VALUES (?, ?)", (uuid, 1))
    conn.commit()
    conn.close()


def _seed_messages_cursor(messages_root: Path, uuid: str, partition: str = "projects/alpha") -> None:
    conn = _db.connect(messages_root / "ccmsg.db", ddl=_MESSAGES_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO cursors (session_uuid, partition, high_water_message_id) "
        "VALUES (?, ?, ?)",
        (uuid, partition, "20260701T000000Z-0001"),
    )
    conn.commit()
    conn.close()


def _seed_sessions_row(sessions_dir: Path, uuid: str, ts: str) -> None:
    conn = _db.connect(sessions_dir / "sessions.db", ddl=_SESSIONS_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO session_tags (uuid, tag, updated_at) VALUES (?, ?, ?)",
        (uuid, "t", ts),
    )
    conn.commit()
    conn.close()


def _make_session_env(session_env_dir: Path, uuid: str, *, mtime: float | None = None) -> None:
    d = session_env_dir / uuid
    d.mkdir(parents=True, exist_ok=True)
    if mtime is not None:
        os.utime(d, (mtime, mtime))


def _reconcile_uuids(scheduler_dir: Path) -> set[str]:
    conn = _db.connect(scheduler_dir / "ccsched.db", readonly=True)
    try:
        return {r["session_uuid"] for r in conn.execute("SELECT session_uuid FROM reconcile_throttle")}
    finally:
        conn.close()


def _scheduler_cursor_uuids(scheduler_dir: Path) -> set[str]:
    conn = _db.connect(scheduler_dir / "ccsched.db", readonly=True)
    try:
        return {r["session_uuid"] for r in conn.execute("SELECT session_uuid FROM cursors")}
    finally:
        conn.close()


def _messages_cursor_uuids(messages_root: Path) -> set[str]:
    conn = _db.connect(messages_root / "ccmsg.db", readonly=True)
    try:
        return {r["session_uuid"] for r in conn.execute("SELECT DISTINCT session_uuid FROM cursors")}
    finally:
        conn.close()


def _session_tags_uuids(sessions_dir: Path) -> set[str]:
    conn = _db.connect(sessions_dir / "sessions.db", readonly=True)
    try:
        return {r["uuid"] for r in conn.execute("SELECT uuid FROM session_tags")}
    finally:
        conn.close()


# TypedDict, not a bare dict[str, Path], because prune() (unlike build_report(),
# which test_ccst_gc_report.py's identically-shaped fixture spreads into fine)
# takes heterogeneously-typed keyword args (float, bool, frozenset, plus these
# 5 Path ones) — mypy can't verify a **dict[str, Path] spread against those
# other parameter types, but a **TypedDict spread type-checks key-by-key.
class _GcDirs(TypedDict):
    projects_dir: Path
    scheduler_dir: Path
    messages_root: Path
    session_env_dir: Path
    sessions_dir: Path


@pytest.fixture
def gc_dirs(tmp_path: Path) -> _GcDirs:
    return _GcDirs(
        projects_dir=tmp_path / "projects",
        scheduler_dir=tmp_path / "cc-scheduler",
        messages_root=tmp_path / "cc-messages",
        session_env_dir=tmp_path / "session-env",
        sessions_dir=tmp_path / "sessions",
    )


def _store(report: PruneReport, name: str) -> StorePruneResult:
    return next(s for s in report.stores if s.name == name)


# ---------- _uuid_age_hours: most-recent-of-3-sources wins ----------


def test_uuid_age_hours_prefers_most_recent_signal() -> None:
    now = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
    old = "2026-08-01T00:00:00Z"     # 26 days old
    recent = "2026-08-27T06:00:00Z"  # 6 hours old

    ages = _uuid_age_hours(
        reconcile_ts={"u1": old},
        session_tags_ts={"u1": recent},
        session_env_mtimes={},
        now=now,
    )

    assert ages["u1"] == pytest.approx(6.0, abs=0.01)


def test_uuid_age_hours_uses_session_env_mtime() -> None:
    now = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
    two_hours_ago = (now - timedelta(hours=2)).timestamp()

    ages = _uuid_age_hours(
        reconcile_ts={},
        session_tags_ts={},
        session_env_mtimes={"u1": two_hours_ago},
        now=now,
    )

    assert ages["u1"] == pytest.approx(2.0, abs=0.01)


def test_uuid_age_hours_uuid_absent_everywhere_has_no_entry() -> None:
    ages = _uuid_age_hours({}, {}, {}, now=datetime.now(timezone.utc))
    assert "u1" not in ages


# ---------- dry-run makes no changes ----------


def test_dry_run_deletes_nothing(gc_dirs: _GcDirs) -> None:
    _seed_reconcile(gc_dirs["scheduler_dir"], ORPHAN_UUID, OLD_TS)

    report = prune(min_age_hours=24, execute=False, **gc_dirs)

    assert report.executed is False
    assert _store(report, "scheduler-reconcile-markers").deleted == 1  # "would delete"
    assert ORPHAN_UUID in _reconcile_uuids(gc_dirs["scheduler_dir"])  # still there


# ---------- --execute deletes eligible entries ----------


def test_execute_deletes_eligible_orphan(gc_dirs: _GcDirs) -> None:
    _seed_reconcile(gc_dirs["scheduler_dir"], ORPHAN_UUID, OLD_TS)

    report = prune(min_age_hours=24, execute=True, **gc_dirs)

    assert report.executed is True
    store = _store(report, "scheduler-reconcile-markers")
    assert store.deleted == 1
    assert store.failed == 0
    assert ORPHAN_UUID not in _reconcile_uuids(gc_dirs["scheduler_dir"])


def test_execute_never_touches_live_uuid(gc_dirs: _GcDirs) -> None:
    proj = gc_dirs["projects_dir"] / "-some-encoded-cwd"
    proj.mkdir(parents=True)
    (proj / f"{OTHER_UUID}.jsonl").write_text('{"type": "summary"}\n')
    _seed_reconcile(gc_dirs["scheduler_dir"], OTHER_UUID, OLD_TS)

    report = prune(min_age_hours=24, execute=True, **gc_dirs)

    assert _store(report, "scheduler-reconcile-markers").deleted == 0
    assert OTHER_UUID in _reconcile_uuids(gc_dirs["scheduler_dir"])


# ---------- age floor: too-young orphans survive --execute ----------


def test_age_floor_skips_young_orphan(gc_dirs: _GcDirs) -> None:
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID, _recent_ts())

    report = prune(min_age_hours=24, execute=True, **gc_dirs)

    store = _store(report, "sessions-index")
    assert store.deleted == 0
    assert store.skipped_too_young == 1
    assert ORPHAN_UUID in _session_tags_uuids(gc_dirs["sessions_dir"])


# ---------- age-unknown: uuid with no timestamp signal anywhere is never deleted ----------


def test_age_unknown_uuid_survives_execute_even_with_zero_floor(gc_dirs: _GcDirs) -> None:
    # ORPHAN_UUID appears only in the two untimestamped `cursors` tables — no
    # reconcile_throttle row, no session_tags row, no session-env dir — so it
    # has no age signal anywhere. min_age_hours=0 would let any *known* age
    # through; it must NOT let an unknown one through.
    _seed_scheduler_cursor(gc_dirs["scheduler_dir"], ORPHAN_UUID)
    _seed_messages_cursor(gc_dirs["messages_root"], ORPHAN_UUID)

    report = prune(min_age_hours=0, execute=True, **gc_dirs)

    cursor_store = _store(report, "scheduler-cursors")
    messages_store = _store(report, "messages-cursors")
    assert cursor_store.deleted == 0
    assert cursor_store.skipped_age_unknown == 1
    assert messages_store.deleted == 0
    assert messages_store.skipped_age_unknown == 1
    assert ORPHAN_UUID in _scheduler_cursor_uuids(gc_dirs["scheduler_dir"])
    assert ORPHAN_UUID in _messages_cursor_uuids(gc_dirs["messages_root"])


def test_age_unknown_resolved_once_a_timestamped_store_has_the_uuid(gc_dirs: _GcDirs) -> None:
    # Same untimestamped-only uuid as above, but this time it ALSO has an old
    # reconcile_throttle row — the merged age map now has a signal for it, so
    # scheduler-cursors can use that signal even though its own table has none.
    _seed_scheduler_cursor(gc_dirs["scheduler_dir"], ORPHAN_UUID)
    _seed_reconcile(gc_dirs["scheduler_dir"], ORPHAN_UUID, OLD_TS)

    report = prune(min_age_hours=24, execute=True, **gc_dirs)

    cursor_store = _store(report, "scheduler-cursors")
    assert cursor_store.skipped_age_unknown == 0
    assert cursor_store.deleted == 1
    assert ORPHAN_UUID not in _scheduler_cursor_uuids(gc_dirs["scheduler_dir"])


# ---------- partial-failure isolation ----------


def test_one_store_failure_does_not_block_others(gc_dirs: _GcDirs, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_reconcile(gc_dirs["scheduler_dir"], ORPHAN_UUID, OLD_TS)
    _seed_sessions_row(gc_dirs["sessions_dir"], YOUNG_UUID, OLD_TS)  # eligible, different store

    def _boom(_uuid: str) -> None:
        raise RuntimeError("simulated delete failure")

    import cc_session_tools.lib.session_gc as session_gc

    monkeypatch.setattr(session_gc, "_delete_scheduler_reconcile", _boom)

    report = prune(min_age_hours=24, execute=True, **gc_dirs)

    reconcile_store = _store(report, "scheduler-reconcile-markers")
    sessions_store = _store(report, "sessions-index")
    assert reconcile_store.failed == 1
    assert reconcile_store.deleted == 0
    assert ORPHAN_UUID in _reconcile_uuids(gc_dirs["scheduler_dir"])  # not deleted, delete raised
    assert sessions_store.deleted == 1  # unaffected by the other store's failure
    assert YOUNG_UUID not in _session_tags_uuids(gc_dirs["sessions_dir"])


# ---------- session-env: the one filesystem (non-SQL) delete ----------


def test_execute_deletes_session_env_directory(gc_dirs: _GcDirs) -> None:
    old_mtime = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    _make_session_env(gc_dirs["session_env_dir"], ORPHAN_UUID, mtime=old_mtime)

    report = prune(min_age_hours=24, execute=True, **gc_dirs)

    store = _store(report, "session-env")
    assert store.deleted == 1
    assert not (gc_dirs["session_env_dir"] / ORPHAN_UUID).exists()


# ---------- --only filtering ----------


def test_only_restricts_to_named_stores(gc_dirs: _GcDirs) -> None:
    _seed_reconcile(gc_dirs["scheduler_dir"], ORPHAN_UUID, OLD_TS)
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID, OLD_TS)

    report = prune(
        min_age_hours=24, execute=True, only=frozenset({"sessions-index"}), **gc_dirs
    )

    assert [s.name for s in report.stores] == ["sessions-index"]
    assert ORPHAN_UUID not in _session_tags_uuids(gc_dirs["sessions_dir"])
    assert ORPHAN_UUID in _reconcile_uuids(gc_dirs["scheduler_dir"])  # untouched, not selected


# ---------- format_report's new suggestion line ----------


def test_format_report_suggests_prune_when_orphans_exist(gc_dirs: _GcDirs) -> None:
    _seed_reconcile(gc_dirs["scheduler_dir"], ORPHAN_UUID, OLD_TS)
    report = build_report(**gc_dirs)
    assert "Run `ccst gc prune`" in format_report(report)


def test_format_report_omits_prune_suggestion_when_no_orphans(gc_dirs: _GcDirs) -> None:
    report = build_report(**gc_dirs)
    assert report.total_orphaned == 0
    assert "Run `ccst gc prune`" not in format_report(report)


# ---------- CLI integration ----------


def test_cli_gc_prune_dry_run_exits_ok_and_does_not_delete(gc_dirs: _GcDirs) -> None:
    _seed_reconcile(gc_dirs["scheduler_dir"], ORPHAN_UUID, OLD_TS)

    result = _run(
        "gc", "prune",
        "--projects-dir", str(gc_dirs["projects_dir"]),
        "--scheduler-dir", str(gc_dirs["scheduler_dir"]),
        "--messages-root", str(gc_dirs["messages_root"]),
        "--session-env-dir", str(gc_dirs["session_env_dir"]),
        "--sessions-dir", str(gc_dirs["sessions_dir"]),
    )

    assert result.returncode == 0
    assert "--execute" in result.stdout
    assert ORPHAN_UUID in _reconcile_uuids(gc_dirs["scheduler_dir"])


def test_cli_gc_prune_execute_deletes_and_exits_ok(gc_dirs: _GcDirs) -> None:
    _seed_reconcile(gc_dirs["scheduler_dir"], ORPHAN_UUID, OLD_TS)

    result = _run(
        "gc", "prune", "--execute",
        "--projects-dir", str(gc_dirs["projects_dir"]),
        "--scheduler-dir", str(gc_dirs["scheduler_dir"]),
        "--messages-root", str(gc_dirs["messages_root"]),
        "--session-env-dir", str(gc_dirs["session_env_dir"]),
        "--sessions-dir", str(gc_dirs["sessions_dir"]),
    )

    assert result.returncode == 0
    assert "Deleted 1" in result.stdout
    assert ORPHAN_UUID not in _reconcile_uuids(gc_dirs["scheduler_dir"])


def test_cli_gc_prune_min_age_hours_flag(gc_dirs: _GcDirs) -> None:
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID, _recent_ts())

    result = _run(
        "gc", "prune", "--execute", "--min-age-hours", "24",
        "--projects-dir", str(gc_dirs["projects_dir"]),
        "--scheduler-dir", str(gc_dirs["scheduler_dir"]),
        "--messages-root", str(gc_dirs["messages_root"]),
        "--session-env-dir", str(gc_dirs["session_env_dir"]),
        "--sessions-dir", str(gc_dirs["sessions_dir"]),
    )

    assert result.returncode == 0
    assert ORPHAN_UUID in _session_tags_uuids(gc_dirs["sessions_dir"])  # too young, kept


def test_cli_gc_prune_only_flag(gc_dirs: _GcDirs) -> None:
    _seed_reconcile(gc_dirs["scheduler_dir"], ORPHAN_UUID, OLD_TS)
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID, OLD_TS)

    result = _run(
        "gc", "prune", "--execute", "--only", "sessions-index",
        "--projects-dir", str(gc_dirs["projects_dir"]),
        "--scheduler-dir", str(gc_dirs["scheduler_dir"]),
        "--messages-root", str(gc_dirs["messages_root"]),
        "--session-env-dir", str(gc_dirs["session_env_dir"]),
        "--sessions-dir", str(gc_dirs["sessions_dir"]),
    )

    assert result.returncode == 0
    assert ORPHAN_UUID not in _session_tags_uuids(gc_dirs["sessions_dir"])
    assert ORPHAN_UUID in _reconcile_uuids(gc_dirs["scheduler_dir"])


def test_gc_prune_report_type_is_prunereport(gc_dirs: _GcDirs) -> None:
    report = prune(**gc_dirs)
    assert isinstance(report, PruneReport)
