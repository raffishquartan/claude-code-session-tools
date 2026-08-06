"""Tests for ccst shell install/uninstall and cc_session_tools.lib.shell_install."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cc_session_tools.lib.shell_install import (
    FRAGMENT_FILENAME,
    RCAction,
    RCResult,
    install_all,
    install_fragment,
    uninstall_all,
    uninstall_fragment,
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


# ---------- install_fragment ----------

def test_install_fragment_creates_missing_dir(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    result = install_fragment(fragments_dir, apply=True)
    assert result.action == RCAction.ADDED
    assert fragments_dir.is_dir()
    assert (fragments_dir / FRAGMENT_FILENAME).exists()


def test_install_fragment_writes_ccl_function(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    install_fragment(fragments_dir, apply=True)
    content = (fragments_dir / FRAGMENT_FILENAME).read_text()
    assert "ccl()" in content
    assert "ccl-global" in content
    assert "ccl-recent" in content


def test_install_fragment_dry_run_does_not_write(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    result = install_fragment(fragments_dir, apply=False)
    assert result.action == RCAction.ADDED
    assert not fragments_dir.exists()


def test_install_fragment_idempotent_when_already_present(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    install_fragment(fragments_dir, apply=True)
    result = install_fragment(fragments_dir, apply=True)
    assert result.action == RCAction.ALREADY_PRESENT


def test_install_fragment_replaces_outdated_fragment(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    fragments_dir.mkdir()
    (fragments_dir / FRAGMENT_FILENAME).write_text("old-function() {}\n")
    result = install_fragment(fragments_dir, apply=True)
    assert result.action == RCAction.REPLACED
    content = (fragments_dir / FRAGMENT_FILENAME).read_text()
    assert "old-function" not in content
    assert "ccl()" in content


def test_install_fragment_function_uses_portable_syntax(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    install_fragment(fragments_dir, apply=True)
    content = (fragments_dir / FRAGMENT_FILENAME).read_text()
    # Portable bash/zsh syntax: name() { ... }
    assert "ccl() {" in content
    # NOT bash-only: function ccl { ... }
    assert "function ccl {" not in content


def test_install_fragment_does_not_touch_sibling_files(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    fragments_dir.mkdir()
    sibling = fragments_dir / "other-tool.sh"
    sibling.write_text("# unrelated fragment\n")
    install_fragment(fragments_dir, apply=True)
    assert sibling.read_text() == "# unrelated fragment\n"


# ---------- uninstall_fragment ----------

def test_uninstall_fragment_reports_not_present_when_dir_missing(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    result = uninstall_fragment(fragments_dir, apply=True)
    assert result.action == RCAction.NOT_PRESENT


def test_uninstall_fragment_reports_not_present_when_file_missing(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    fragments_dir.mkdir()
    result = uninstall_fragment(fragments_dir, apply=True)
    assert result.action == RCAction.NOT_PRESENT


def test_uninstall_fragment_removes_file(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    install_fragment(fragments_dir, apply=True)
    result = uninstall_fragment(fragments_dir, apply=True)
    assert result.action == RCAction.REMOVED
    assert not (fragments_dir / FRAGMENT_FILENAME).exists()


def test_uninstall_fragment_dry_run_does_not_remove(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    install_fragment(fragments_dir, apply=True)
    result = uninstall_fragment(fragments_dir, apply=False)
    assert result.action == RCAction.REMOVED
    assert (fragments_dir / FRAGMENT_FILENAME).exists()  # not actually removed


def test_uninstall_fragment_preserves_sibling_files(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    install_fragment(fragments_dir, apply=True)
    sibling = fragments_dir / "other-tool.sh"
    sibling.write_text("# unrelated fragment\n")
    uninstall_fragment(fragments_dir, apply=True)
    assert not (fragments_dir / FRAGMENT_FILENAME).exists()
    assert sibling.exists()


# ---------- install_all / uninstall_all ----------

def test_install_all_creates_each_dir(tmp_path: Path) -> None:
    a = tmp_path / "a" / ".shellrc.d"
    b = tmp_path / "b" / ".shellrc.d"
    results = install_all([a, b], apply=True)
    actions = {r.path.parent: r.action for r in results}
    assert actions[a] == RCAction.ADDED
    assert actions[b] == RCAction.ADDED


def test_uninstall_all_reports_not_present_when_missing(tmp_path: Path) -> None:
    results = uninstall_all([tmp_path / "a" / ".shellrc.d", tmp_path / "b" / ".shellrc.d"], apply=True)
    assert all(r.action == RCAction.NOT_PRESENT for r in results)


# ---------- CLI integration ----------

def test_shell_install_dry_run(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    result = _run("shell", "install", "--fragments-dir", str(fragments_dir))
    assert result.returncode == 0
    assert "Dry run" in result.stdout
    assert not fragments_dir.exists()


def test_shell_install_apply(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    result = _run("shell", "install", "--fragments-dir", str(fragments_dir), "--apply")
    assert result.returncode == 0
    assert "ccl()" in (fragments_dir / FRAGMENT_FILENAME).read_text()


def test_shell_uninstall_dry_run(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    _run("shell", "install", "--fragments-dir", str(fragments_dir), "--apply")
    result = _run("shell", "uninstall", "--fragments-dir", str(fragments_dir))
    assert result.returncode == 0
    assert "Dry run" in result.stdout
    assert (fragments_dir / FRAGMENT_FILENAME).exists()  # not removed


def test_shell_uninstall_apply(tmp_path: Path) -> None:
    fragments_dir = tmp_path / ".shellrc.d"
    _run("shell", "install", "--fragments-dir", str(fragments_dir), "--apply")
    result = _run("shell", "uninstall", "--fragments-dir", str(fragments_dir), "--apply")
    assert result.returncode == 0
    assert not (fragments_dir / FRAGMENT_FILENAME).exists()


def test_shell_no_subcommand_errors() -> None:
    result = _run("shell")
    assert result.returncode != 0


# ---------- fragment content sanity checks ----------

def test_fragment_contains_opened_and_active_choices(tmp_path: Path) -> None:
    """The fragment's help text must advertise opened and active as --order-by choices."""
    fragments_dir = tmp_path / ".shellrc.d"
    install_fragment(fragments_dir, apply=True)
    content = (fragments_dir / FRAGMENT_FILENAME).read_text()
    assert "opened" in content
    assert "active" in content


def test_fragment_contains_ccl_recent(tmp_path: Path) -> None:
    """ccl-recent shorthand must be present in the installed fragment."""
    fragments_dir = tmp_path / ".shellrc.d"
    install_fragment(fragments_dir, apply=True)
    content = (fragments_dir / FRAGMENT_FILENAME).read_text()
    assert "ccl-recent" in content


def test_fragment_contains_saw_global_and_saw_order_by_logic(tmp_path: Path) -> None:
    """ccl function must inject --order-by active when --global given without --order-by."""
    fragments_dir = tmp_path / ".shellrc.d"
    install_fragment(fragments_dir, apply=True)
    content = (fragments_dir / FRAGMENT_FILENAME).read_text()
    assert "_saw_global" in content
    assert "_saw_order_by" in content
    assert "--order-by active" in content


def test_reinstalled_fragment_contains_opened_and_active(tmp_path: Path) -> None:
    """After reinstalling over an outdated fragment, the new content includes opened/active choices."""
    fragments_dir = tmp_path / ".shellrc.d"
    fragments_dir.mkdir()
    (fragments_dir / FRAGMENT_FILENAME).write_text("old-function() {}\n")
    result = install_fragment(fragments_dir, apply=True)
    assert result.action == RCAction.REPLACED
    content = (fragments_dir / FRAGMENT_FILENAME).read_text()
    assert "opened" in content
    assert "active" in content
    assert "ccl-recent" in content
