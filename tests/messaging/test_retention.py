# tests/messaging/test_retention.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging import repository, retention
from cc_session_tools.lib.messaging.message import Message


def _write(partition: str, mid: str, status: str, stamp: str | None) -> None:
    repository.insert(Message(
        id=mid, schema=1, from_project="x", from_session="x", from_uuid="s",
        to_kind="project", to_value="alpha", to_location=partition, subject="s",
        sent_at="2026-06-01T00:00:00Z", status=status,  # type: ignore[arg-type]
        read_at=stamp, read_by_uuid="r" if stamp else None,
        read_by_session="r" if stamp else None, claimed_at=None,
        receipt_shown=False, thread=None, attachments=[], body="b",
    ))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_unread_never_archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    _write("projects/alpha", "20260101T000000Z-0001", "sent", None)
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    assert retention.archive_old("projects/alpha", now) == []
    m = repository.get_by_id("20260101T000000Z-0001")
    assert m is not None and m.status == "sent"


def test_read_13_days_old_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    _write("projects/alpha", "20260101T000000Z-0001", "read", _iso(now - timedelta(days=13)))
    assert retention.archive_old("projects/alpha", now) == []
    m = repository.get_by_id("20260101T000000Z-0001")
    assert m is not None and m.status == "read"


def test_read_15_days_old_is_archived(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    _write("projects/alpha", "20260101T000000Z-0001", "read", _iso(now - timedelta(days=15)))
    assert retention.archive_old("projects/alpha", now) == ["20260101T000000Z-0001"]
    m = repository.get_by_id("20260101T000000Z-0001")
    assert m is not None and m.status == "archived"


def test_claimed_15_days_old_is_archived_via_claimed_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCST_MESSAGES_ROOT", str(tmp_path))
    now = datetime(2026, 6, 20, tzinfo=timezone.utc)
    # status=claimed with read_at unset: eligibility must come from claimed_at.
    repository.insert(Message(
        id="20260101T000000Z-0002", schema=1, from_project="x", from_session="x",
        from_uuid="s", to_kind="description", to_value="whoever", to_location="projects/alpha",
        subject="s", sent_at="2026-06-01T00:00:00Z", status="claimed",
        read_at=None, read_by_uuid=None, read_by_session=None,
        claimed_at=_iso(now - timedelta(days=15)), receipt_shown=False,
        thread=None, attachments=[], body="b",
    ))
    assert retention.archive_old("projects/alpha", now) == ["20260101T000000Z-0002"]
    m = repository.get_by_id("20260101T000000Z-0002")
    assert m is not None and m.status == "archived"
