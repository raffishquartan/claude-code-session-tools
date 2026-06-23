"""Tests for cccs_hooks.no_emdash."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from cccs_hooks import no_emdash
from cccs_hooks.no_emdash import _CORRECTION_MSG, _SENTINEL, check, main
from cccs_hooks.transcript import Turn, TranscriptNotFound


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _turn(role: str, content: str) -> Turn:
    return Turn(role=role, content=content, timestamp="2026-01-01T00:00:00Z")


# ---------------------------------------------------------------------------
# check() — em-dash detection and injection logic
# ---------------------------------------------------------------------------

class TestCheck:
    def test_empty_turns_returns_0(self) -> None:
        assert check([]) == 0

    def test_no_assistant_turn_returns_0(self) -> None:
        turns = [_turn("user", "hello")]
        assert check(turns) == 0

    def test_clean_assistant_response_returns_0(self) -> None:
        turns = [
            _turn("user", "hello"),
            _turn("assistant", "A clean response with no em-dashes."),
        ]
        assert check(turns) == 0

    def test_em_dash_in_assistant_returns_2(self, capsys) -> None:
        turns = [
            _turn("user", "hello"),
            _turn("assistant", "Great point — and here is why."),
        ]
        rc = check(turns)
        assert rc == 2

    def test_correction_msg_printed_to_stdout_on_detection(self, capsys) -> None:
        turns = [
            _turn("user", "hello"),
            _turn("assistant", "Great point — and here is why."),
        ]
        check(turns)
        captured = capsys.readouterr()
        assert _CORRECTION_MSG in captured.out

    def test_sentinel_printed_to_stderr_on_detection(self, capsys) -> None:
        turns = [
            _turn("user", "hello"),
            _turn("assistant", "The result — a success."),
        ]
        check(turns)
        captured = capsys.readouterr()
        assert _SENTINEL in captured.err

    def test_infinite_loop_guard_when_prev_user_has_sentinel(self) -> None:
        turns = [
            _turn("user", "original prompt"),
            _turn("assistant", "First reply — with em-dash."),
            _turn("user", f"{_SENTINEL} Your previous response..."),
            _turn("assistant", "Correction: First reply - with em-dash."),
        ]
        assert check(turns) == 0

    def test_guard_only_checks_user_before_last_assistant(self) -> None:
        # Sentinel appears in a very old user turn but NOT the one immediately
        # before the last assistant turn — should still detect the em-dash.
        turns = [
            _turn("user", f"Earlier turn with {_SENTINEL} in it."),
            _turn("assistant", "Some intermediate response."),
            _turn("user", "Normal follow-up question."),
            _turn("assistant", "Another reply — with em-dash."),
        ]
        assert check(turns) == 2

    def test_em_dash_in_older_assistant_turn_not_flagged(self) -> None:
        # Last assistant turn is clean; an old one had em-dash.
        turns = [
            _turn("user", "first"),
            _turn("assistant", "Old reply — with em-dash."),
            _turn("user", "second"),
            _turn("assistant", "Clean reply with no em-dash."),
        ]
        assert check(turns) == 0

    def test_multiple_em_dashes_still_returns_2(self, capsys) -> None:
        turns = [
            _turn("user", "tell me"),
            _turn("assistant", "One — two — three."),
        ]
        assert check(turns) == 2

    def test_only_tool_use_turns_between_user_and_no_text(self) -> None:
        # tool_use synthetic turns have role "tool_use" not "assistant", so
        # _last_assistant_text returns None when there is no real assistant turn.
        turns = [
            _turn("user", "call a tool"),
            _turn("tool_use", ""),
        ]
        assert check(turns) == 0

    def test_assistant_with_only_tool_use_blocks_has_empty_text(self) -> None:
        # If _extract_text returned "" for a tool-only assistant message,
        # there are no em-dashes in "" — should return 0.
        turns = [
            _turn("user", "do something"),
            _turn("assistant", ""),  # empty text (tool-only block)
        ]
        assert check(turns) == 0


# ---------------------------------------------------------------------------
# main() — stdin parsing and transcript loading
# ---------------------------------------------------------------------------

class TestMain:
    def test_empty_stdin_returns_0(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        assert main() == 0

    def test_malformed_json_returns_0(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("not-json"))
        assert main() == 0
        assert "malformed" in capsys.readouterr().err

    def test_missing_session_id_returns_0(self, monkeypatch) -> None:
        payload = json.dumps({"cwd": "/some/dir"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        assert main() == 0

    def test_missing_cwd_returns_0(self, monkeypatch) -> None:
        payload = json.dumps({"session_id": "abc-123"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        assert main() == 0

    def test_transcript_not_found_returns_0(self, monkeypatch, capsys) -> None:
        payload = json.dumps({"session_id": "abc-123", "cwd": "/fake"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        with patch("cccs_hooks.no_emdash.load_transcript", side_effect=TranscriptNotFound("missing")):
            rc = main()
        assert rc == 0
        assert "transcript not found" in capsys.readouterr().err

    def test_clean_transcript_returns_0(self, monkeypatch) -> None:
        payload = json.dumps({"session_id": "abc-123", "cwd": "/proj"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        clean_turns = [
            _turn("user", "hi"),
            _turn("assistant", "Hello, no em-dashes here."),
        ]
        with patch("cccs_hooks.no_emdash.load_transcript", return_value=clean_turns):
            rc = main()
        assert rc == 0

    def test_em_dash_transcript_returns_2(self, monkeypatch, capsys) -> None:
        payload = json.dumps({"session_id": "abc-123", "cwd": "/proj"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        emdash_turns = [
            _turn("user", "hi"),
            _turn("assistant", "A point — worth noting."),
        ]
        with patch("cccs_hooks.no_emdash.load_transcript", return_value=emdash_turns):
            rc = main()
        assert rc == 2
        assert _SENTINEL in capsys.readouterr().out

    def test_claude_project_dir_env_preferred_over_cwd(self, monkeypatch) -> None:
        payload = json.dumps({"session_id": "abc-123", "cwd": "/wrong"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/correct")
        captured_cwd: list[str] = []

        def fake_load(*, session_id: str, cwd: str) -> list[Turn]:
            captured_cwd.append(cwd)
            return []

        with patch("cccs_hooks.no_emdash.load_transcript", side_effect=fake_load):
            main()

        assert captured_cwd == ["/correct"]

    def test_non_dict_json_returns_0(self, monkeypatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([1, 2, 3])))
        assert main() == 0
