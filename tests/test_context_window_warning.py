import json
from pathlib import Path

import pytest

from cccs_hooks import context_window_warning as cww


def _write_transcript(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def test_fresh_session_has_zero_tokens(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "user", "message": {"content": "hi"}}])
    assert cww._current_context_tokens(p) == (0, "")


def test_reads_last_assistant_usage(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant", "message": {"model": "claude-sonnet-5",
            "usage": {"input_tokens": 100, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 5000}}},
        {"type": "assistant", "message": {"model": "claude-sonnet-5",
            "usage": {"input_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 154800}}},
    ])
    assert cww._current_context_tokens(p) == (155000, "claude-sonnet-5")


def test_sidechain_turns_are_skipped(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant", "message": {"model": "claude-sonnet-5",
            "usage": {"input_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
        {"type": "assistant", "isSidechain": True, "message": {"model": "claude-haiku-4-5",
            "usage": {"input_tokens": 9, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
    ])
    assert cww._current_context_tokens(p) == (100, "claude-sonnet-5")


def test_blank_lines_tolerated(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text('\n{"type": "user", "message": {"content": "hi"}}\n\n')
    assert cww._current_context_tokens(p) == (0, "")


def test_missing_transcript_raises(tmp_path: Path):
    with pytest.raises(cww.TranscriptError):
        cww._current_context_tokens(tmp_path / "nope.jsonl")


def test_corrupt_transcript_raises(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text("not json at all\nstill not json\n")
    with pytest.raises(cww.TranscriptError):
        cww._current_context_tokens(p)
