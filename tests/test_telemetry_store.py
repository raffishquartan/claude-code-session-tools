from __future__ import annotations

import re
from pathlib import Path

import pytest

from cc_session_tools.lib import telemetry_store


def test_db_path_uses_hooks_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path))
    assert telemetry_store.db_path() == tmp_path / "telemetry.db"


def test_db_path_falls_back_to_default_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CCCS_HOOKS_DIR", raising=False)
    monkeypatch.setattr(telemetry_store, "_DEFAULT_HOOKS_DIR", tmp_path)
    assert telemetry_store.db_path() == tmp_path / "telemetry.db"


def test_db_path_explicit_dir_beats_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "env-dir"))
    explicit = tmp_path / "explicit-dir"
    assert telemetry_store.db_path(explicit) == explicit / "telemetry.db"


def test_connect_creates_both_tables(tmp_path: Path) -> None:
    conn = telemetry_store.connect(tmp_path)
    try:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"telemetry_events", "catchup_events"} <= tables
    finally:
        conn.close()


def test_connect_applies_wal_pragma(tmp_path: Path) -> None:
    conn = telemetry_store.connect(tmp_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_now_iso_is_utc_z_suffixed() -> None:
    ts = telemetry_store.now_iso()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts)


def test_checkpoint_and_vacuum_does_not_raise_on_fresh_db(tmp_path: Path) -> None:
    conn = telemetry_store.connect(tmp_path)
    try:
        telemetry_store.checkpoint_and_vacuum(conn)  # must not raise
    finally:
        conn.close()
