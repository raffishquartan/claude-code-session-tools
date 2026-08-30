from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import sync_notify
from cc_session_tools.lib.scheduler import ledger


@pytest.fixture(autouse=True)
def _dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test in this module from the real telemetry.db/ccsched.db, matching
    tests/scheduler/test_surface.py's fixture. Autouse and unconditional (not opt-in per test) so
    a test cannot forget it and silently write a real ledger row - see the incident this module's
    own WORKLOG records: a test that omitted this originally wrote real
    "pdata-sync:myproject" rows into the developer's actual ~/.local/share/claude/telemetry.db."""
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))
    monkeypatch.setenv("CC_SCHEDULER_DIR", str(tmp_path / "sched"))


def _spy_send() -> tuple[list[str], sync_notify.TelegramSend]:
    calls: list[str] = []

    def send(message: str) -> bool:
        calls.append(message)
        return True

    return calls, send


def test_notify_conflict_sends_telegram_message_naming_project_and_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, send = _spy_send()
    monkeypatch.setattr(sync_notify, "send_telegram", send)
    sync_notify.notify_conflict("myproject", outcome="fork", detail="two writers diverged")
    assert len(calls) == 1
    assert "myproject" in calls[0]
    assert "fork" in calls[0]
    assert "two writers diverged" in calls[0]


def test_notify_conflict_records_ledger_row_for_the_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, send = _spy_send()
    monkeypatch.setattr(sync_notify, "send_telegram", send)
    sync_notify.notify_conflict(
        "myproject", outcome="checksum_invalid", detail="dump checksum did not match"
    )
    rows = ledger.read_recent(job_id="pdata-sync:myproject")
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "run"
    assert row["exit_code"] != 0
    assert row["error"] is not None
    assert "checksum_invalid" in str(row["error"])
    assert "dump checksum did not match" in str(row["error"])


def test_notify_conflict_ledger_row_surfaces_as_findings_in_the_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of writing to the ledger rather than only pushing Telegram: a conflict
    discovered with no session open (Task 13's hourly ccsched job) must still show up in the
    next SessionStart's catch-up digest, via the same surface()/format_digest() pipeline every
    other bundled job's output already goes through."""
    from cc_session_tools.lib.scheduler import surface
    from cc_session_tools.lib.scheduler.digest import format_digest

    calls, send = _spy_send()
    monkeypatch.setattr(sync_notify, "send_telegram", send)
    sync_notify.notify_conflict("myproject", outcome="fork", detail="two writers diverged")

    result = surface.surface(session_uuid="test-session", now=datetime.now(timezone.utc))
    digest = format_digest(result.reports)
    assert "pdata-sync:myproject" in digest
    assert "fork" in digest
    assert "two writers diverged" in digest


def test_notify_conflict_does_not_raise_when_telegram_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_send(message: str) -> bool:
        return False

    monkeypatch.setattr(sync_notify, "send_telegram", failing_send)
    # Must not raise - a notification that can't be sent must never break the
    # caller (CLI command, hook, or cron job) it is reporting from.
    sync_notify.notify_conflict("myproject", outcome="fork", detail="boom")
    rows = ledger.read_recent(job_id="pdata-sync:myproject")
    assert len(rows) == 1


def test_notify_conflict_does_not_raise_when_ledger_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point CCCS_HOOKS_DIR at a path that can't hold a sqlite file (a file, not a dir),
    # so the underlying ledger.record() call's own internal try/except degrades to a
    # logged warning rather than raising - notify_conflict must survive that too.
    unusable = tmp_path / "not-a-dir"
    unusable.write_text("blocking file")
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(unusable))
    calls, send = _spy_send()
    monkeypatch.setattr(sync_notify, "send_telegram", send)
    sync_notify.notify_conflict("myproject", outcome="fork", detail="boom")
    assert len(calls) == 1
