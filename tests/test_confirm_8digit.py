from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from cccs_hooks.confirm_8digit import GATED_TOOLS_DEFAULT, verify
from cccs_hooks.confirm_8digit import markers_dir as confirm_8digit_markers_dir
from cccs_hooks.marker_allow import markers_dir as marker_allow_markers_dir


# ---------- transcript builder ----------


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


def _assistant_text(
    text: str, ts: str = "2026-05-10T12:00:00.000Z"
) -> dict[str, object]:
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


def _hook_input(
    tool_name: str = "mcp__whatsapp__send_message",
    tool_input: dict[str, object] | None = None,
    session_id: str = "sid",
    cwd: str = "/tmp/x",
) -> dict[str, object]:
    return {
        "tool_name": tool_name,
        "tool_input": tool_input or {"recipient": "n", "message": "m"},
        "session_id": session_id,
        "cwd": cwd,
    }


# ---------- non-gated tools are never verified ----------


def test_non_gated_tool_allowed_without_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool not in gated_tools must be allowed unconditionally - no
    transcript lookup, no 8-digit check - even in block mode. Otherwise a
    no-matcher catch-all registration would block every tool call."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    # session_id "missing" => no transcript. A gated tool would fail closed
    # (exit 2); a non-gated tool must short-circuit to allow (exit 0) first.
    for tool in ("Read", "Write", "Edit", "Bash"):
        result = verify(
            _hook_input(tool_name=tool, tool_input={}, session_id="missing"),
            GATED_TOOLS_DEFAULT,
        )
        assert result.exit_code == 0, f"{tool} should be allowed unconditionally"
        assert result.message == "", f"{tool} should produce no warning"


# ---------- transcript missing ----------


def test_block_when_transcript_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "warn")
    result = verify(_hook_input(session_id="missing"), GATED_TOOLS_DEFAULT)
    # Fail-closed even in warn mode.
    assert result.exit_code == 2
    assert "transcript not found" in result.message.lower()


# ---------- happy path ----------


def test_allow_when_three_conditions_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    _write_transcript(
        tmp_path,
        "/tmp/x",
        "sid",
        [
            _assistant_text(
                "Respond with 12345678 only if you want me to send.",
                ts="2026-05-10T12:00:00.000Z",
            ),
            _user("12345678", ts="2026-05-10T12:00:30.000Z"),
        ],
    )
    result = verify(_hook_input(), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 0
    assert "verified" in result.message


# ---------- failure modes ----------


def test_block_when_no_recent_8digit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    _write_transcript(
        tmp_path,
        "/tmp/x",
        "sid",
        [
            _assistant_text("Respond with 12345678 ...", ts="2026-05-10T12:00:00.000Z"),
            _user("hello", ts="2026-05-10T12:00:30.000Z"),
        ],
    )
    result = verify(_hook_input(), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 2
    assert "8 digits" in result.message


def test_block_replay_attempt_with_stale_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if the transcript contains an earlier 12345678 user turn, the
    LATEST user turn must be the eight digits. A later 'send another' user
    turn must invalidate the gate (replay defence)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    _write_transcript(
        tmp_path,
        "/tmp/x",
        "sid",
        [
            _assistant_text("Respond with 12345678 ...", ts="2026-05-10T12:00:00.000Z"),
            _user("12345678", ts="2026-05-10T12:00:30.000Z"),
            _assistant_text("done", ts="2026-05-10T12:00:35.000Z"),
            _user("now send another one", ts="2026-05-10T12:00:40.000Z"),
        ],
    )
    result = verify(_hook_input(), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 2


def test_block_when_no_preceding_offer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    _write_transcript(
        tmp_path,
        "/tmp/x",
        "sid",
        [
            _assistant_text("acknowledged", ts="2026-05-10T12:00:00.000Z"),
            _user("12345678", ts="2026-05-10T12:00:30.000Z"),
        ],
    )
    result = verify(_hook_input(), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 2
    assert "respond with" in result.message.lower()


def test_block_when_gated_tool_already_fired_in_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    _write_transcript(
        tmp_path,
        "/tmp/x",
        "sid",
        [
            _assistant_text(
                "Respond with 12345678 ...", ts="2026-05-10T12:00:00.000Z"
            ),
            _user("12345678", ts="2026-05-10T12:00:30.000Z"),
            _assistant_tool_use(
                "mcp__whatsapp__send_message", ts="2026-05-10T12:00:31.000Z"
            ),
        ],
    )
    result = verify(_hook_input(), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 2
    assert "replay" in result.message.lower()


def test_block_when_reply_gap_exceeds_30_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    _write_transcript(
        tmp_path,
        "/tmp/x",
        "sid",
        [
            _assistant_text(
                "Respond with 12345678 ...", ts="2026-05-10T12:00:00.000Z"
            ),
            _user("12345678", ts="2026-05-10T13:00:00.000Z"),
        ],
    )
    result = verify(_hook_input(), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 2
    assert "30 minutes" in result.message


# ---------- enforcement modes ----------


def test_warn_mode_exits_zero_for_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "warn")
    _write_transcript(
        tmp_path,
        "/tmp/x",
        "sid",
        [_user("hi", ts="2026-05-10T12:00:30.000Z")],
    )
    result = verify(_hook_input(), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 0
    assert "[8digit-warn]" in result.message


def test_block_mode_exits_two_for_failed_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    _write_transcript(
        tmp_path,
        "/tmp/x",
        "sid",
        [_user("hi", ts="2026-05-10T12:00:30.000Z")],
    )
    result = verify(_hook_input(), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 2


# ---------- skill-marker exceptions ----------


def test_marker_exception_tesco_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    markers = tmp_path / ".cache" / "claude" / "markers"
    monkeypatch.setenv("CCCS_MARKERS_DIR", str(markers))
    markers.mkdir(parents=True)
    (markers / "tesco_shop_active").write_text("")
    # No transcript exists - if marker did not short-circuit we'd block.
    result = verify(
        _hook_input(
            tool_name="mcp__opentabs__tesco_create_order",
            tool_input={},
            session_id="any",
        ),
        GATED_TOOLS_DEFAULT,
    )
    assert result.exit_code == 0
    assert "tesco_shop_active" in result.message


def test_marker_default_dir_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With CCCS_MARKERS_DIR and XDG_CACHE_HOME both unset, _markers_dir()
    must default to ``$HOME/.cache/claude/markers``. This exercises the
    env-UNSET default branch, not just the explicit override."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CCCS_MARKERS_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    markers = tmp_path / ".cache" / "claude" / "markers"
    markers.mkdir(parents=True)
    (markers / "tesco_shop_active").write_text("")
    # No transcript exists - if the default-path marker is not found we'd block.
    result = verify(
        _hook_input(
            tool_name="mcp__opentabs__tesco_create_order",
            tool_input={},
            session_id="any",
        ),
        GATED_TOOLS_DEFAULT,
    )
    assert result.exit_code == 0
    assert "tesco_shop_active" in result.message


def test_marker_exception_expired_treated_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    markers = tmp_path / ".cache" / "claude" / "markers"
    monkeypatch.setenv("CCCS_MARKERS_DIR", str(markers))
    markers.mkdir(parents=True)
    f = markers / "tesco_shop_active"
    f.write_text("")
    # Backdate the marker to 2 hours ago.
    two_hours_ago = time.time() - 2 * 3600
    os.utime(f, (two_hours_ago, two_hours_ago))
    # Marker is stale, so we fall through to the transcript check, which
    # is missing - that should fail closed (exit 2).
    result = verify(
        _hook_input(
            tool_name="mcp__opentabs__tesco_create_order",
            tool_input={},
            session_id="missing",
        ),
        GATED_TOOLS_DEFAULT,
    )
    assert result.exit_code == 2


def test_marker_telegram_requires_recipient_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654")
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    markers = tmp_path / ".cache" / "claude" / "markers"
    monkeypatch.setenv("CCCS_MARKERS_DIR", str(markers))
    markers.mkdir(parents=True)
    (markers / "telegram_notify").write_text("")
    # Wrong recipient - should NOT match the marker.
    result = verify(
        _hook_input(
            tool_name="mcp__whatsapp__send_message",
            tool_input={"recipient": "OTHER", "message": "x"},
            session_id="missing",
        ),
        GATED_TOOLS_DEFAULT,
    )
    assert result.exit_code == 2


def test_marker_calendar_sync_email_requires_subject_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    markers = tmp_path / ".cache" / "claude" / "markers"
    monkeypatch.setenv("CCCS_MARKERS_DIR", str(markers))
    markers.mkdir(parents=True)
    (markers / "calendar_sync_email").write_text("")
    result_ok = verify(
        _hook_input(
            tool_name="mcp__google-workspace__send_gmail_message",
            tool_input={"subject": "[Cld] Calendar sync 2026-05-10"},
            session_id="missing",
        ),
        GATED_TOOLS_DEFAULT,
    )
    assert result_ok.exit_code == 0

    result_bad = verify(
        _hook_input(
            tool_name="mcp__google-workspace__send_gmail_message",
            tool_input={"subject": "Something else"},
            session_id="missing",
        ),
        GATED_TOOLS_DEFAULT,
    )
    assert result_bad.exit_code == 2


# ---------- shared markers_dir resolution ----------


def test_confirm_8digit_and_marker_allow_share_markers_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """confirm_8digit and marker_allow both import markers_dir from
    cccs_hooks.markers - it is the identical function, so a marker refreshed
    via marker-allow's auto-approved touch is always seen as fresh by
    confirm_8digit's exemption check."""
    assert confirm_8digit_markers_dir is marker_allow_markers_dir

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CCCS_MARKERS_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    expected = tmp_path / ".cache" / "claude" / "markers"
    assert confirm_8digit_markers_dir() == expected
    assert marker_allow_markers_dir() == expected


def test_shared_markers_dir_honours_cccs_markers_dir_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom-markers"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_MARKERS_DIR", str(custom))
    assert confirm_8digit_markers_dir() == custom
    assert marker_allow_markers_dir() == custom


# ---------- self-send exception ----------


def _self_send_input(
    **overrides: object,
) -> dict[str, object]:
    """A Gmail send to/from the same self address, with no transcript -
    so it blocks (exit 2) unless the self-send exemption short-circuits."""
    tool_input: dict[str, object] = {
        "to": "me@example.com",
        "user_google_email": "me@example.com",
        "subject": "anything",
        "body": "x",
    }
    tool_input.update(overrides)
    return _hook_input(
        tool_name="mcp__google-workspace__send_gmail_message",
        tool_input=tool_input,
        session_id="missing",
    )


def test_self_send_exempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """to == from == NOTIFY_EMAIL with no cc/bcc is exempt even with no
    transcript and in block mode."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    monkeypatch.setenv("NOTIFY_EMAIL", "me@example.com")
    result = verify(_self_send_input(), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 0
    assert "self-send" in result.message


def test_self_send_alias_sender_exempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit from_email Send-As alias equal to the self address still
    counts as a self-send."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    monkeypatch.setenv("NOTIFY_EMAIL", "me@example.com")
    result = verify(
        _self_send_input(user_google_email="svc@example.com", from_email="me@example.com"),
        GATED_TOOLS_DEFAULT,
    )
    assert result.exit_code == 0


def test_self_send_case_insensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    monkeypatch.setenv("NOTIFY_EMAIL", "Me@Example.com")
    result = verify(
        _self_send_input(to="me@example.com", user_google_email="ME@EXAMPLE.COM"),
        GATED_TOOLS_DEFAULT,
    )
    assert result.exit_code == 0


def test_self_send_with_cc_not_exempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    monkeypatch.setenv("NOTIFY_EMAIL", "me@example.com")
    result = verify(_self_send_input(cc="other@example.com"), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 2


def test_self_send_with_bcc_not_exempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    monkeypatch.setenv("NOTIFY_EMAIL", "me@example.com")
    result = verify(_self_send_input(bcc="other@example.com"), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 2


def test_send_to_other_recipient_not_exempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    monkeypatch.setenv("NOTIFY_EMAIL", "me@example.com")
    result = verify(_self_send_input(to="other@example.com"), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 2


def test_self_send_no_notify_email_not_exempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With NOTIFY_EMAIL unset the exemption must not fire (fail closed)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCCS_ENFORCE_8DIGIT", "block")
    monkeypatch.delenv("NOTIFY_EMAIL", raising=False)
    result = verify(_self_send_input(), GATED_TOOLS_DEFAULT)
    assert result.exit_code == 2
