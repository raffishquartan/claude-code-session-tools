from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.scheduler import notify


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
