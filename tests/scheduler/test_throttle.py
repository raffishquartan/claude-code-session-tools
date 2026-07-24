from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import throttle

UTC = timezone.utc


def test_read_missing_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    assert throttle.read_last_reconciled("u") is None


def test_stamp_then_read_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    now = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    throttle.stamp_reconciled("u", now)
    assert throttle.read_last_reconciled("u") == now


def test_stamp_is_idempotent_upsert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    throttle.stamp_reconciled("u", datetime(2026, 6, 20, 10, 0, tzinfo=UTC))
    later = datetime(2026, 6, 20, 10, 5, tzinfo=UTC)
    throttle.stamp_reconciled("u", later)
    assert throttle.read_last_reconciled("u") == later


def test_per_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path))
    a = datetime(2026, 6, 20, 10, 0, tzinfo=UTC)
    b = datetime(2026, 6, 20, 11, 0, tzinfo=UTC)
    throttle.stamp_reconciled("a", a)
    throttle.stamp_reconciled("b", b)
    assert throttle.read_last_reconciled("a") == a
    assert throttle.read_last_reconciled("b") == b
