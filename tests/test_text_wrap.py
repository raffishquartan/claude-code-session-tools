"""Shared terminal-width wrapping used by `ccst doctor` and the hooks install output."""
from __future__ import annotations

import os
import shutil

import pytest

from cc_session_tools.lib import text_wrap


def _width(monkeypatch: pytest.MonkeyPatch, columns: int) -> None:
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=(80, 24): os.terminal_size((columns, 24))
    )


def test_wraps_to_detected_width(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 80)
    out = text_wrap.wrap_block("word " * 60)
    lines = out.splitlines()
    assert len(lines) > 1
    assert all(len(line) <= 80 for line in lines)


def test_wide_terminal_wraps_to_fewer_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "word " * 60
    _width(monkeypatch, 80)
    narrow = len(text_wrap.wrap_block(text).splitlines())
    _width(monkeypatch, 200)
    wide = len(text_wrap.wrap_block(text).splitlines())
    assert wide < narrow


def test_every_line_carries_the_indent(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 60)
    lines = text_wrap.wrap_block("word " * 40, indent="      ").splitlines()
    assert len(lines) > 1
    assert all(line.startswith("      ") and not line.startswith("       ") for line in lines)


def test_width_floor_is_40_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 20)
    assert text_wrap.effective_width() == 40
    lines = text_wrap.wrap_block("word " * 40).splitlines()
    assert max(len(line) for line in lines) > 20
    assert all(len(line) <= 40 for line in lines)


def test_non_tty_fallback_is_80_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=(80, 24): os.terminal_size(fallback)
    )
    assert text_wrap.effective_width() == 80


def test_path_and_hyphenated_name_are_never_split(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 40)
    path = "/Users/x/.local/share/claude/claude-flags.json"
    text = "not yet created; will be created at " + path + " on first use"
    out = text_wrap.wrap_block(text)
    assert path in out.split()
    assert "context-window-warning" in text_wrap.wrap_block("nudge from " * 3 + "context-window-warning")


def test_overlong_token_overflows_unsplit(monkeypatch: pytest.MonkeyPatch) -> None:
    _width(monkeypatch, 40)
    token = "x" * 70
    lines = text_wrap.wrap_block(f"see {token} now").splitlines()
    assert any(line.strip() == token for line in lines)
