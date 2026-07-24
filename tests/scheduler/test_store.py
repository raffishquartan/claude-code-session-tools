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
