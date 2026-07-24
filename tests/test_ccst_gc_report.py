"""Tests for ccst gc report and cc_session_tools.lib.session_gc."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib import db as _db
from cc_session_tools.lib.session_gc import (
    GcReport,
    build_report,
    format_report,
    known_session_uuids,
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


LIVE_UUID = "11111111-1111-1111-1111-111111111111"
ORPHAN_UUID = "22222222-2222-2222-2222-222222222222"

# SQLite's own WAL/shm sidecar files are transient bookkeeping that a read-only
# connection may legitimately create or drop while reading a WAL-mode database.
# They are not part of the "never deletes or modifies the stored data" contract,
# so the immutability snapshots below ignore them.
_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _is_sqlite_sidecar(p: Path) -> bool:
    return any(p.name.endswith(suffix) for suffix in _SQLITE_SIDECAR_SUFFIXES)


# ccsched.db: TWO separate tables (Phase 3 lib/scheduler/store.py _DDL) — NOT one combined table.
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
# ccmsg.db: composite-keyed cursors, one row per (session_uuid, partition) (Phase 2).
_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS cursors (
    session_uuid          TEXT NOT NULL,
    partition             TEXT NOT NULL,
    high_water_message_id TEXT NOT NULL,
    PRIMARY KEY (session_uuid, partition)
);
"""
# sessions.db: uuids live in session_tags, NOT in the (project_dir, basename)-keyed sessions table (Phase 4).
_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS session_tags (
    uuid       TEXT PRIMARY KEY,
    tag        TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _make_transcript(projects_dir: Path, uuid: str) -> None:
    proj = projects_dir / "-some-encoded-cwd"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{uuid}.jsonl").write_text('{"type": "summary"}\n')


def _seed_scheduler_row(
    scheduler_dir: Path, uuid: str, *, reconcile: bool = False, cursor: bool = False
) -> None:
    conn = _db.connect(scheduler_dir / "ccsched.db", ddl=_SCHEDULER_DDL)
    if reconcile:
        conn.execute(
            "INSERT OR REPLACE INTO reconcile_throttle (session_uuid, last_reconciled_at) "
            "VALUES (?, ?)",
            (uuid, "2026-07-01T00:00:00Z"),
        )
    if cursor:
        conn.execute(
            "INSERT OR REPLACE INTO cursors (session_uuid, offset) VALUES (?, ?)", (uuid, 42)
        )
    conn.commit()
    conn.close()


def _seed_messages_cursor(messages_root: Path, uuid: str, *, partition: str = "projects/alpha") -> None:
    conn = _db.connect(messages_root / "ccmsg.db", ddl=_MESSAGES_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO cursors (session_uuid, partition, high_water_message_id) "
        "VALUES (?, ?, ?)",
        (uuid, partition, "20260701T000000Z-0001"),
    )
    conn.commit()
    conn.close()


def _seed_sessions_row(sessions_dir: Path, uuid: str) -> None:
    conn = _db.connect(sessions_dir / "sessions.db", ddl=_SESSIONS_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO session_tags (uuid, tag, updated_at) VALUES (?, ?, ?)",
        (uuid, "t", "2026-07-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()


def _make_session_env(session_env_dir: Path, uuid: str) -> None:
    (session_env_dir / uuid).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def gc_dirs(tmp_path: Path) -> dict[str, Path]:
    return {
        "projects_dir": tmp_path / "projects",
        "scheduler_dir": tmp_path / "cc-scheduler",
        "messages_root": tmp_path / "cc-messages",
        "session_env_dir": tmp_path / "session-env",
        "sessions_dir": tmp_path / "sessions",
    }


# ---------- known_session_uuids ----------


def test_known_session_uuids_reads_jsonl_stems(tmp_path: Path) -> None:
    _make_transcript(tmp_path, LIVE_UUID)
    assert known_session_uuids(tmp_path) == {LIVE_UUID}


def test_known_session_uuids_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert known_session_uuids(tmp_path / "does-not-exist") == set()


# ---------- build_report: live uuid never orphaned ----------


def test_live_uuid_not_orphaned_in_any_store(gc_dirs: dict[str, Path]) -> None:
    _make_transcript(gc_dirs["projects_dir"], LIVE_UUID)
    _seed_scheduler_row(gc_dirs["scheduler_dir"], LIVE_UUID, reconcile=True, cursor=True)
    _seed_messages_cursor(gc_dirs["messages_root"], LIVE_UUID)
    _make_session_env(gc_dirs["session_env_dir"], LIVE_UUID)
    _seed_sessions_row(gc_dirs["sessions_dir"], LIVE_UUID)

    report = build_report(**gc_dirs)

    assert report.known_uuid_count == 1
    for store in report.stores:
        assert store.total == 1
        assert store.orphaned == 0
        assert LIVE_UUID not in store.orphaned_uuids


# ---------- build_report: uuid with no transcript IS orphaned ----------


def test_orphan_uuid_reported_in_every_store(gc_dirs: dict[str, Path]) -> None:
    # No transcript at all for ORPHAN_UUID.
    _seed_scheduler_row(gc_dirs["scheduler_dir"], ORPHAN_UUID, reconcile=True, cursor=True)
    _seed_messages_cursor(gc_dirs["messages_root"], ORPHAN_UUID)
    _make_session_env(gc_dirs["session_env_dir"], ORPHAN_UUID)
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID)

    report = build_report(**gc_dirs)

    assert report.known_uuid_count == 0
    for store in report.stores:
        assert store.total == 1
        assert store.orphaned == 1
        assert ORPHAN_UUID in store.orphaned_uuids


def test_mixed_live_and_orphan_uuids(gc_dirs: dict[str, Path]) -> None:
    _make_transcript(gc_dirs["projects_dir"], LIVE_UUID)
    _seed_scheduler_row(gc_dirs["scheduler_dir"], LIVE_UUID, reconcile=True)
    _seed_scheduler_row(gc_dirs["scheduler_dir"], ORPHAN_UUID, reconcile=True)

    report = build_report(**gc_dirs)

    reconcile_store = next(s for s in report.stores if s.name == "scheduler-reconcile-markers")
    assert reconcile_store.total == 2
    assert reconcile_store.orphaned == 1
    assert reconcile_store.orphaned_uuids == (ORPHAN_UUID,)


def test_scheduler_cursor_and_reconcile_are_independent_dimensions(gc_dirs: dict[str, Path]) -> None:
    """A row with only a cursor set (no reconcile throttle) counts for
    scheduler-cursors but not scheduler-reconcile-markers, and vice versa."""
    _seed_scheduler_row(gc_dirs["scheduler_dir"], LIVE_UUID, cursor=True)  # no reconcile

    report = build_report(**gc_dirs)

    reconcile_store = next(s for s in report.stores if s.name == "scheduler-reconcile-markers")
    cursor_store = next(s for s in report.stores if s.name == "scheduler-cursors")
    assert reconcile_store.total == 0
    assert cursor_store.total == 1


def test_messages_cursor_multi_partition_session_counts_once(gc_dirs: dict[str, Path]) -> None:
    _make_transcript(gc_dirs["projects_dir"], LIVE_UUID)
    _seed_messages_cursor(gc_dirs["messages_root"], LIVE_UUID, partition="projects/alpha")
    _seed_messages_cursor(gc_dirs["messages_root"], LIVE_UUID, partition="projects/beta")

    report = build_report(**gc_dirs)

    messages_store = next(s for s in report.stores if s.name == "messages-cursors")
    assert messages_store.total == 1     # two cursor rows, one distinct session uuid
    assert messages_store.orphaned == 0


def test_new_sessions_index_store_reports_orphans(gc_dirs: dict[str, Path]) -> None:
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID)

    report = build_report(**gc_dirs)

    sessions_store = next(s for s in report.stores if s.name == "sessions-index")
    assert sessions_store.total == 1
    assert sessions_store.orphaned == 1


# ---------- session-env directory (dir-per-uuid, not file-per-uuid) ----------


def test_empty_session_env_dir_for_orphan_uuid_is_counted(gc_dirs: dict[str, Path]) -> None:
    # session-env entries are directories, and may be empty (no files inside
    # them) — the directory's existence alone is what identifies the uuid.
    empty_dir = gc_dirs["session_env_dir"] / ORPHAN_UUID
    empty_dir.mkdir(parents=True)
    assert list(empty_dir.iterdir()) == []

    report = build_report(**gc_dirs)

    session_env_store = next(s for s in report.stores if s.name == "session-env")
    assert session_env_store.total == 1
    assert session_env_store.orphaned == 1
    assert session_env_store.orphaned_uuids == (ORPHAN_UUID,)


def test_session_env_ignores_non_directory_entries(gc_dirs: dict[str, Path]) -> None:
    gc_dirs["session_env_dir"].mkdir(parents=True)
    (gc_dirs["session_env_dir"] / "stray-file.txt").write_text("not a session")

    report = build_report(**gc_dirs)

    session_env_store = next(s for s in report.stores if s.name == "session-env")
    assert session_env_store.total == 0


# ---------- missing stores don't error ----------


def test_build_report_all_stores_missing_returns_zeroes(gc_dirs: dict[str, Path]) -> None:
    report = build_report(**gc_dirs)
    assert report.known_uuid_count == 0
    for store in report.stores:
        assert store.total == 0
        assert store.orphaned == 0


# ---------- format_report ----------


def test_format_report_includes_store_names_and_counts(gc_dirs: dict[str, Path]) -> None:
    _seed_scheduler_row(gc_dirs["scheduler_dir"], ORPHAN_UUID, reconcile=True)
    report = build_report(**gc_dirs)
    text = format_report(report)
    assert "scheduler-reconcile-markers" in text
    assert "Report-only" in text
    assert "1" in text  # the orphan count appears somewhere


# ---------- never deletes/modifies anything ----------


def test_build_report_never_deletes_or_modifies_files(gc_dirs: dict[str, Path]) -> None:
    _make_transcript(gc_dirs["projects_dir"], LIVE_UUID)
    _seed_scheduler_row(gc_dirs["scheduler_dir"], LIVE_UUID, reconcile=True, cursor=True)
    _seed_scheduler_row(gc_dirs["scheduler_dir"], ORPHAN_UUID, reconcile=True, cursor=True)
    _seed_messages_cursor(gc_dirs["messages_root"], ORPHAN_UUID)
    _seed_sessions_row(gc_dirs["sessions_dir"], LIVE_UUID)
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID)
    _make_session_env(gc_dirs["session_env_dir"], LIVE_UUID)
    _make_session_env(gc_dirs["session_env_dir"], ORPHAN_UUID)

    def _snapshot() -> dict[str, tuple[bytes, float]]:
        out: dict[str, tuple[bytes, float]] = {}
        for base in gc_dirs.values():
            if not base.is_dir():
                continue
            for p in base.rglob("*"):
                if p.is_file() and not _is_sqlite_sidecar(p):
                    out[str(p)] = (p.read_bytes(), p.stat().st_mtime)
        return out

    before = _snapshot()
    build_report(**gc_dirs)
    after = _snapshot()

    assert before == after


def test_cli_gc_report_never_deletes_or_modifies_files(gc_dirs: dict[str, Path]) -> None:
    _make_transcript(gc_dirs["projects_dir"], LIVE_UUID)
    _seed_scheduler_row(gc_dirs["scheduler_dir"], LIVE_UUID, reconcile=True)
    _seed_scheduler_row(gc_dirs["scheduler_dir"], ORPHAN_UUID, reconcile=True)
    _seed_messages_cursor(gc_dirs["messages_root"], ORPHAN_UUID)
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID)
    _make_session_env(gc_dirs["session_env_dir"], ORPHAN_UUID)

    def _file_set() -> set[str]:
        out: set[str] = set()
        for base in gc_dirs.values():
            if not base.is_dir():
                continue
            out |= {
                str(p)
                for p in base.rglob("*")
                if (p.is_file() or p.is_dir()) and not _is_sqlite_sidecar(p)
            }
        return out

    before = _file_set()
    result = _run(
        "gc", "report",
        "--projects-dir", str(gc_dirs["projects_dir"]),
        "--scheduler-dir", str(gc_dirs["scheduler_dir"]),
        "--messages-root", str(gc_dirs["messages_root"]),
        "--session-env-dir", str(gc_dirs["session_env_dir"]),
        "--sessions-dir", str(gc_dirs["sessions_dir"]),
    )
    after = _file_set()

    assert result.returncode == 0
    assert before == after


# ---------- CLI integration ----------


def test_cli_gc_report_exits_ok_with_no_stores(gc_dirs: dict[str, Path]) -> None:
    result = _run(
        "gc", "report",
        "--projects-dir", str(gc_dirs["projects_dir"]),
        "--scheduler-dir", str(gc_dirs["scheduler_dir"]),
        "--messages-root", str(gc_dirs["messages_root"]),
        "--session-env-dir", str(gc_dirs["session_env_dir"]),
        "--sessions-dir", str(gc_dirs["sessions_dir"]),
    )
    assert result.returncode == 0
    assert "Report-only" in result.stdout


def test_cli_gc_report_reports_orphan_counts(gc_dirs: dict[str, Path]) -> None:
    _seed_scheduler_row(gc_dirs["scheduler_dir"], ORPHAN_UUID, reconcile=True, cursor=True)
    _seed_messages_cursor(gc_dirs["messages_root"], ORPHAN_UUID)
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID)
    _make_session_env(gc_dirs["session_env_dir"], ORPHAN_UUID)

    result = _run(
        "gc", "report",
        "--projects-dir", str(gc_dirs["projects_dir"]),
        "--scheduler-dir", str(gc_dirs["scheduler_dir"]),
        "--messages-root", str(gc_dirs["messages_root"]),
        "--session-env-dir", str(gc_dirs["session_env_dir"]),
        "--sessions-dir", str(gc_dirs["sessions_dir"]),
    )
    assert result.returncode == 0
    assert "scheduler-reconcile-markers" in result.stdout
    assert "scheduler-cursors" in result.stdout
    assert "messages-cursors" in result.stdout
    assert "session-env" in result.stdout
    assert "sessions-index" in result.stdout


def test_cli_gc_report_accepts_sessions_dir_flag(gc_dirs: dict[str, Path]) -> None:
    _seed_sessions_row(gc_dirs["sessions_dir"], ORPHAN_UUID)

    result = _run(
        "gc", "report",
        "--projects-dir", str(gc_dirs["projects_dir"]),
        "--scheduler-dir", str(gc_dirs["scheduler_dir"]),
        "--messages-root", str(gc_dirs["messages_root"]),
        "--session-env-dir", str(gc_dirs["session_env_dir"]),
        "--sessions-dir", str(gc_dirs["sessions_dir"]),
    )
    assert result.returncode == 0
    assert "sessions-index" in result.stdout


def test_gc_report_type_is_gcreport(gc_dirs: dict[str, Path]) -> None:
    report = build_report(**gc_dirs)
    assert isinstance(report, GcReport)
