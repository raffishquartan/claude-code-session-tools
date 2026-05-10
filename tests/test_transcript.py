from __future__ import annotations

import json
from pathlib import Path

import pytest

from cccs_hooks.transcript import (
    TranscriptNotFound,
    Turn,
    find_intervening_gated_call,
    find_preceding_assistant_offer,
    find_recent_8digit,
    load_transcript,
)


# ---------- helpers ----------


def _write_transcript(
    home: Path,
    cwd: str,
    session_id: str,
    records: list[dict[str, object]],
) -> Path:
    encoded = cwd.replace("/", "-")
    proj = home / ".claude" / "projects" / encoded
    proj.mkdir(parents=True, exist_ok=True)
    p = proj / f"{session_id}.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return p


def _user(text: str, ts: str = "2026-05-10T12:00:00.000Z") -> dict[str, object]:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"role": "user", "content": text},
    }


def _assistant_text(text: str, ts: str = "2026-05-10T12:00:00.000Z") -> dict[str, object]:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
        },
    }


def _assistant_tool_use(
    name: str, ts: str = "2026-05-10T12:00:00.000Z"
) -> dict[str, object]:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": name, "input": {}, "id": "x"}],
        },
    }


# ---------- load_transcript ----------


def test_load_transcript_raises_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(TranscriptNotFound):
        load_transcript(session_id="nope-uuid", cwd="/some/where")


def test_load_transcript_skips_malformed_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    encoded = "-tmp-x"
    proj = tmp_path / ".claude" / "projects" / encoded
    proj.mkdir(parents=True)
    p = proj / "sid.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(_user("hello")) + "\n")
        fh.write("{not valid json\n")
        fh.write(json.dumps(_assistant_text("hi back")) + "\n")
    turns = load_transcript(session_id="sid", cwd="/tmp/x")
    roles = [t.role for t in turns]
    assert roles == ["user", "assistant"]
    err = capsys.readouterr().err
    assert "malformed JSONL line" in err


# ---------- find_recent_8digit ----------


def test_find_recent_8digit_match() -> None:
    turns = [
        Turn(role="assistant", content="Respond with 12345678 to confirm", timestamp="t"),
        Turn(role="user", content="12345678", timestamp="t"),
    ]
    assert find_recent_8digit(turns) == ("12345678", 1)


def test_find_recent_8digit_no_match_when_extra_text() -> None:
    turns = [
        Turn(role="user", content="12345678 please", timestamp="t"),
    ]
    assert find_recent_8digit(turns) is None


def test_find_recent_8digit_no_match_when_wrong_length() -> None:
    turns = [Turn(role="user", content="1234567", timestamp="t")]
    assert find_recent_8digit(turns) is None


def test_find_recent_8digit_returns_only_latest_user_turn() -> None:
    turns = [
        Turn(role="user", content="12345678", timestamp="t"),
        Turn(role="assistant", content="ack", timestamp="t"),
        Turn(role="user", content="hello", timestamp="t"),
    ]
    assert find_recent_8digit(turns) is None


# ---------- find_preceding_assistant_offer ----------


def test_find_preceding_offer_match() -> None:
    turns = [
        Turn(
            role="assistant",
            content="Respond with 12345678 only if you want to send.",
            timestamp="t",
        ),
        Turn(role="user", content="12345678", timestamp="t"),
    ]
    assert find_preceding_assistant_offer(turns, user_idx=1, code="12345678") is True


def test_find_preceding_offer_no_match_for_different_code() -> None:
    turns = [
        Turn(role="assistant", content="Respond with 11111111", timestamp="t"),
        Turn(role="user", content="22222222", timestamp="t"),
    ]
    assert find_preceding_assistant_offer(turns, user_idx=1, code="22222222") is False


def test_find_preceding_offer_no_assistant_before() -> None:
    turns = [Turn(role="user", content="12345678", timestamp="t")]
    assert find_preceding_assistant_offer(turns, user_idx=0, code="12345678") is False


# ---------- find_intervening_gated_call ----------


def test_find_intervening_gated_call_present() -> None:
    turns = [
        Turn(role="user", content="12345678", timestamp="t"),
        Turn(
            role="tool_use",
            content="",
            timestamp="t",
            tool_name="mcp__whatsapp__send_message",
        ),
    ]
    assert (
        find_intervening_gated_call(
            turns, user_idx=0, gated_tools=["mcp__whatsapp__send_message"]
        )
        is True
    )


def test_find_intervening_gated_call_absent() -> None:
    turns = [
        Turn(role="user", content="12345678", timestamp="t"),
        Turn(role="tool_use", content="", timestamp="t", tool_name="Read"),
    ]
    assert (
        find_intervening_gated_call(
            turns, user_idx=0, gated_tools=["mcp__whatsapp__send_message"]
        )
        is False
    )


def test_find_intervening_gated_call_only_after_user_idx() -> None:
    turns = [
        Turn(
            role="tool_use",
            content="",
            timestamp="t",
            tool_name="mcp__whatsapp__send_message",
        ),
        Turn(role="user", content="12345678", timestamp="t"),
    ]
    assert (
        find_intervening_gated_call(
            turns, user_idx=1, gated_tools=["mcp__whatsapp__send_message"]
        )
        is False
    )
