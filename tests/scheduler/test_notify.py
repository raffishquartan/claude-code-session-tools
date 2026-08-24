from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import notify
from cc_session_tools.lib.scheduler.digest import Outcome


def _spy_post() -> tuple[list[tuple[str, bytes]], notify.Poster]:
    calls: list[tuple[str, bytes]] = []

    def post(url: str, data: bytes) -> None:
        calls.append((url, data))

    return calls, post


def test_send_uses_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat456")
    calls, post = _spy_post()
    assert notify.send_telegram("hello", post=post) is True
    assert len(calls) == 1
    url, data = calls[0]
    assert "tok123" in url
    assert b"hello" in data
    assert b"chat456" in data


def test_send_falls_back_to_creds_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    creds = tmp_path / "creds"
    creds.write_text('export TELEGRAM_BOT_TOKEN="filetok"\nTELEGRAM_CHAT_ID=filechat\n')
    monkeypatch.setenv("CCCS_CREDS_PATH", str(creds))
    calls, post = _spy_post()
    assert notify.send_telegram("hello", post=post) is True
    assert "filetok" in calls[0][0]
    assert b"filechat" in calls[0][1]


def test_send_returns_false_when_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("CCCS_CREDS_PATH", str(tmp_path / "nope"))
    calls, post = _spy_post()
    assert notify.send_telegram("hello", post=post) is False
    assert calls == []


def test_send_returns_false_on_post_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    def failing_post(url: str, data: bytes) -> None:
        raise OSError("network down")

    assert notify.send_telegram("hello", post=failing_post) is False


def test_suspended_message_names_job_and_enable_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    calls, post = _spy_post()
    notify.suspended("ccmsg-dead-letter-sweep", 10, post=post)
    _, data = calls[0]
    assert b"ccmsg-dead-letter-sweep" in data
    assert b"10 consecutive" in data
    assert b"ccsched enable ccmsg-dead-letter-sweep" in data


# ---------- push_outcome ----------


def test_push_outcome_failed_includes_job_id_and_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    calls, post = _spy_post()
    assert notify.push_outcome("cal", Outcome.FAILED, "boom", post=post) is True
    _, data = calls[0]
    assert b"cal" in data
    assert b"failed" in data
    assert b"boom" in data


def test_push_outcome_failed_without_detail_still_pushes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    calls, post = _spy_post()
    assert notify.push_outcome("cal", Outcome.FAILED, None, post=post) is True
    _, data = calls[0]
    assert b"cal" in data
    assert b"failed" in data


def test_push_outcome_ran_without_detail_pushes_bare_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§ correction: RAN always pushes now, whether or not there is captured
    output - `surface` no longer gates it."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    calls, post = _spy_post()
    assert notify.push_outcome("tesco", Outcome.RAN, None, post=post) is True
    _, data = calls[0]
    assert b"tesco" in data
    assert b"ran" in data


def test_push_outcome_ran_with_detail_includes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    calls, post = _spy_post()
    assert notify.push_outcome("verify", Outcome.RAN, "proj-a: OK", post=post) is True
    _, data = calls[0]
    assert b"verify" in data
    assert b"proj-a: OK" in data


def test_push_outcome_returns_false_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("CCCS_CREDS_PATH", str(tmp_path / "nope"))
    calls, post = _spy_post()
    assert notify.push_outcome("tesco", Outcome.RAN, None, post=post) is False
    assert calls == []


def test_push_outcome_returns_false_on_post_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    def failing_post(url: str, data: bytes) -> None:
        raise OSError("network down")

    assert notify.push_outcome("tesco", Outcome.RAN, None, post=failing_post) is False
