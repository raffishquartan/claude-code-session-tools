"""Tests for ccst shell install/uninstall and cc_session_tools.lib.shell_install."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib.shell_install import (
    RCAction,
    RCResult,
    _BLOCK,
    _SENTINEL_END,
    _SENTINEL_START,
    _find_block,
    install_all,
    install_rc,
    uninstall_all,
    uninstall_rc,
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


# ---------- _find_block ----------

def test_find_block_returns_none_when_absent() -> None:
    lines = ["line1\n", "line2\n"]
    assert _find_block(lines) is None


def test_find_block_finds_present_block() -> None:
    content = f"before\n{_SENTINEL_START}\nfoo\n{_SENTINEL_END}\nafter\n"
    lines = content.splitlines(keepends=True)
    span = _find_block(lines)
    assert span is not None
    start, end = span
    assert lines[start].strip() == _SENTINEL_START
    assert lines[end].strip() == _SENTINEL_END


def test_find_block_handles_trailing_spaces() -> None:
    content = f"{_SENTINEL_START}  \nfoo\n{_SENTINEL_END}   \n"
    lines = content.splitlines(keepends=True)
    span = _find_block(lines)
    assert span is not None


# ---------- install_rc ----------

def test_install_rc_skips_missing_file(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    result = install_rc(rc, apply=True)
    assert result.action == RCAction.SKIPPED


def test_install_rc_adds_block_to_empty_file(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("")
    result = install_rc(rc, apply=True)
    assert result.action == RCAction.ADDED
    assert _SENTINEL_START in rc.read_text()
    assert _SENTINEL_END in rc.read_text()
    assert "ccl()" in rc.read_text()


def test_install_rc_adds_block_to_non_empty_file(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("export PATH=/usr/bin:$PATH\n")
    result = install_rc(rc, apply=True)
    assert result.action == RCAction.ADDED
    content = rc.read_text()
    assert "export PATH" in content
    assert "ccl()" in content


def test_install_rc_dry_run_does_not_write(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("existing content\n")
    result = install_rc(rc, apply=False)
    assert result.action == RCAction.ADDED
    assert "ccl()" not in rc.read_text()


def test_install_rc_idempotent_when_already_present(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("")
    install_rc(rc, apply=True)
    result = install_rc(rc, apply=True)
    assert result.action == RCAction.ALREADY_PRESENT
    # Block should appear only once
    content = rc.read_text()
    assert content.count(_SENTINEL_START) == 1


def test_install_rc_replaces_outdated_block(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    old_block = f"{_SENTINEL_START}\nold-function() {{}}\n{_SENTINEL_END}\n"
    rc.write_text(old_block)
    result = install_rc(rc, apply=True)
    assert result.action == RCAction.REPLACED
    content = rc.read_text()
    assert "old-function" not in content
    assert "ccl()" in content


def test_install_rc_function_uses_portable_syntax(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("")
    install_rc(rc, apply=True)
    content = rc.read_text()
    # Portable bash/zsh syntax: name() { ... }
    assert "ccl() {" in content
    # NOT bash-only: function ccl { ... }
    assert "function ccl {" not in content


def test_install_rc_separator_newline_when_no_trailing_newline(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("no newline at end")  # no trailing \n
    install_rc(rc, apply=True)
    content = rc.read_text()
    # The sentinel must be on its own line, not joined to the previous line
    assert _SENTINEL_START in content
    sentinel_idx = content.index(_SENTINEL_START)
    # The character before the sentinel (if any) must be a newline
    if sentinel_idx > 0:
        assert content[sentinel_idx - 1] == "\n"


# ---------- uninstall_rc ----------

def test_uninstall_rc_skips_missing_file(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    result = uninstall_rc(rc, apply=True)
    assert result.action == RCAction.SKIPPED


def test_uninstall_rc_reports_not_present(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("no ccl here\n")
    result = uninstall_rc(rc, apply=True)
    assert result.action == RCAction.NOT_PRESENT


def test_uninstall_rc_removes_block(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("")
    install_rc(rc, apply=True)
    result = uninstall_rc(rc, apply=True)
    assert result.action == RCAction.REMOVED
    content = rc.read_text()
    assert _SENTINEL_START not in content
    assert "ccl()" not in content


def test_uninstall_rc_dry_run_does_not_remove(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("")
    install_rc(rc, apply=True)
    result = uninstall_rc(rc, apply=False)
    assert result.action == RCAction.REMOVED
    assert _SENTINEL_START in rc.read_text()  # not actually removed


def test_uninstall_rc_preserves_surrounding_content(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("before\n")
    install_rc(rc, apply=True)
    rc_with_after = rc.read_text() + "after\n"
    rc.write_text(rc_with_after)
    uninstall_rc(rc, apply=True)
    content = rc.read_text()
    assert "before" in content
    assert "after" in content
    assert "ccl()" not in content


# ---------- install_all / uninstall_all ----------

def test_install_all_only_modifies_existing_files(tmp_path: Path) -> None:
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text("")
    # .zshrc does NOT exist
    results = install_all([bashrc, tmp_path / ".zshrc"], apply=True)
    actions = {r.path.name: r.action for r in results}
    assert actions[".bashrc"] == RCAction.ADDED
    assert actions[".zshrc"] == RCAction.SKIPPED


def test_uninstall_all_skips_missing(tmp_path: Path) -> None:
    results = uninstall_all([tmp_path / ".bashrc", tmp_path / ".zshrc"], apply=True)
    assert all(r.action == RCAction.SKIPPED for r in results)


# ---------- CLI integration ----------

def test_shell_install_dry_run(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("")
    result = _run("shell", "install", "--rc-file", str(rc))
    assert result.returncode == 0
    assert "Dry run" in result.stdout
    assert "ccl()" not in rc.read_text()


def test_shell_install_apply(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("")
    result = _run("shell", "install", "--rc-file", str(rc), "--apply")
    assert result.returncode == 0
    assert "ccl()" in rc.read_text()


def test_shell_uninstall_dry_run(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("")
    _run("shell", "install", "--rc-file", str(rc), "--apply")
    result = _run("shell", "uninstall", "--rc-file", str(rc))
    assert result.returncode == 0
    assert "Dry run" in result.stdout
    assert "ccl()" in rc.read_text()  # not removed


def test_shell_uninstall_apply(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("")
    _run("shell", "install", "--rc-file", str(rc), "--apply")
    result = _run("shell", "uninstall", "--rc-file", str(rc), "--apply")
    assert result.returncode == 0
    assert "ccl()" not in rc.read_text()


def test_shell_no_subcommand_errors() -> None:
    result = _run("shell")
    assert result.returncode != 0
