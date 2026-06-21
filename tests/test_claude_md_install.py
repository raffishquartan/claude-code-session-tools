# tests/test_claude_md_install.py
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.claude_md_install import (
    MalformedBlockError,
    MarkdownAction,
    install_claude_md,
    uninstall_claude_md,
    # White-box: tests assert on the exact sentinel markers the module manages.
    _SENTINEL_START,
    _SENTINEL_END,
)


def test_install_adds_block(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# My instructions\n")
    result = install_claude_md(md, apply=True)
    assert result.action is MarkdownAction.ADDED
    text = md.read_text()
    assert _SENTINEL_START in text and _SENTINEL_END in text
    assert text.startswith("# My instructions")


def test_reinstall_is_idempotent(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=True)
    before = md.read_text()
    result = install_claude_md(md, apply=True)
    assert result.action is MarkdownAction.ALREADY_PRESENT
    assert md.read_text() == before


def test_uninstall_removes_block(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=True)
    result = uninstall_claude_md(md, apply=True)
    assert result.action is MarkdownAction.REMOVED
    assert _SENTINEL_START not in md.read_text()


def test_install_dry_run_does_not_write(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=False)
    assert _SENTINEL_START not in md.read_text()


def test_install_creates_missing_file(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    result = install_claude_md(md, apply=True)
    assert result.action is MarkdownAction.ADDED
    assert md.is_file()


def test_uninstall_dry_run_does_not_write(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=True)
    before = md.read_text()
    result = uninstall_claude_md(md, apply=False)
    assert result.action is MarkdownAction.REMOVED  # would-remove (dry run)
    assert md.read_text() == before  # nothing written


def test_orphaned_start_marker_is_rejected(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    # Only a START marker, with user prose after it: editing must not swallow it.
    md.write_text(f"# x\n{_SENTINEL_START}\nimportant user prose\n")
    with pytest.raises(MalformedBlockError):
        install_claude_md(md, apply=True)
    with pytest.raises(MalformedBlockError):
        uninstall_claude_md(md, apply=True)
    # The file is untouched.
    assert "important user prose" in md.read_text()
