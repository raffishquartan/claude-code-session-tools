from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import paths
from cc_session_tools.lib.scheduler import store


def test_scheduler_dir_honours_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))
    assert store.scheduler_dir() == tmp_path / "sched"


def test_scheduler_dir_defaults_to_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CC_SCHEDULER_DIR", raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "dh"))
    assert store.scheduler_dir() == paths.data_home()
    assert store.scheduler_dir() == tmp_path / "dh"


def test_db_path_is_ccsched_db_in_scheduler_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert store.db_path() == tmp_path / "ccsched.db"


def test_connect_creates_all_four_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    conn = store.connect()
    try:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert {"jobs", "job_state", "cursors", "reconcile_throttle"} <= names


def test_connect_applies_wal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    conn = store.connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_success_exit_codes_column_backfilled_on_pre_existing_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A jobs table created before success_exit_codes existed must gain the
    column (with its default) on the next connect(), not error out - CREATE
    TABLE IF NOT EXISTS alone is a no-op against an already-existing table."""
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    conn = store.connect()
    conn.execute("ALTER TABLE jobs DROP COLUMN success_exit_codes")
    conn.commit()
    conn.close()

    conn2 = store.connect()
    try:
        cols = {r["name"] for r in conn2.execute("PRAGMA table_info(jobs)")}
        assert "success_exit_codes" in cols
        conn2.execute(
            "INSERT INTO jobs (job_id, cadence, coalesce_kind, command, surface, "
            "enabled, catchup_window, timeout) VALUES ('j','daily@09:00','one','[]',1,1,'7d','60s')"
        )
        conn2.commit()
        row = conn2.execute("SELECT success_exit_codes FROM jobs WHERE job_id='j'").fetchone()
        assert row["success_exit_codes"] == "[0]"
    finally:
        conn2.close()
