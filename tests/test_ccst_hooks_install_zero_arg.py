"""Tests for ccst hooks install zero-arg (bundle auto-discovery) and --hook selector."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=cwd or str(Path(__file__).parent.parent),
    )


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


# ---------- bundle discovery ----------

def test_hooks_install_no_source_uses_bundle(tmp_path: Path) -> None:
    """When --source is omitted, the bundled hooks-bundle.json is auto-discovered."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--target", str(tgt))
    assert result.returncode == 0, result.stderr
    # Should see hook names in output
    assert "session-tag" in result.stdout
    assert "Dry run" in result.stdout


def test_hooks_install_no_source_dry_run_does_not_modify(tmp_path: Path) -> None:
    """Dry run with auto-discovered bundle must not write to target."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    _run("hooks", "install", "--target", str(tgt))
    assert json.loads(tgt.read_text()) == {}


def test_hooks_install_no_source_apply_writes_bundle(tmp_path: Path) -> None:
    """--apply with auto-discovered bundle writes all bundled hooks."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--target", str(tgt), "--apply")
    assert result.returncode == 0, result.stderr
    written = json.loads(tgt.read_text())
    # At minimum session-tag should be present
    hooks_section = written.get("hooks", {})
    assert hooks_section, "Expected hooks to be written"
    # Collect all commands
    all_cmds = set()
    for blocks in hooks_section.values():
        for block in blocks:
            for h in block.get("hooks", []):
                all_cmds.add(h.get("command", ""))
    assert "ccst hooks run session-tag" in all_cmds


def test_hooks_install_bundle_all_hooks(tmp_path: Path) -> None:
    """The bundle installs the full set of hooks."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--target", str(tgt), "--apply")
    assert result.returncode == 0, result.stderr
    written = json.loads(tgt.read_text())
    all_cmds = set()
    for blocks in written.get("hooks", {}).values():
        for block in blocks:
            for h in block.get("hooks", []):
                all_cmds.add(h.get("command", ""))
    expected = {
        "ccst hooks run bash-security-review",
        "ccst hooks run confirm-8digit",
        "ccst hooks run prompt-guard",
        "ccst hooks run edit-write-audit",
        "ccst hooks run session-end",
        "ccst hooks run session-tag",
        "ccst hooks run last-screenshot",
        "ccst hooks run messaging-deliver",
    }
    assert expected == all_cmds, f"Missing: {expected - all_cmds}; extra: {all_cmds - expected}"


def test_hooks_install_idempotent(tmp_path: Path) -> None:
    """Re-running install on an already-installed target reports 'nothing to add'."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    _run("hooks", "install", "--target", str(tgt), "--apply")
    result = _run("hooks", "install", "--target", str(tgt), "--apply")
    assert result.returncode == 0, result.stderr
    assert "nothing to add" in result.stdout.lower() or "up to date" in result.stdout.lower()


# ---------- --hook selector ----------

def test_hooks_install_hook_selector_installs_only_named_hook(tmp_path: Path) -> None:
    """--hook session-tag installs only that one hook."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--hook", "session-tag", "--target", str(tgt), "--apply")
    assert result.returncode == 0, result.stderr
    written = json.loads(tgt.read_text())
    all_cmds = set()
    for blocks in written.get("hooks", {}).values():
        for block in blocks:
            for h in block.get("hooks", []):
                all_cmds.add(h.get("command", ""))
    assert all_cmds == {"ccst hooks run session-tag"}, f"Unexpected cmds: {all_cmds}"


def test_hooks_install_hook_selector_dry_run(tmp_path: Path) -> None:
    """--hook with dry run shows the hook in output but doesn't write."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--hook", "session-tag", "--target", str(tgt))
    assert result.returncode == 0, result.stderr
    assert "session-tag" in result.stdout
    assert "Dry run" in result.stdout
    assert json.loads(tgt.read_text()) == {}


def test_hooks_install_invalid_hook_name_errors(tmp_path: Path) -> None:
    """--hook with an unknown name exits non-zero with an informative message."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--hook", "nonexistent-hook", "--target", str(tgt))
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "known" in result.stderr.lower()


def test_hooks_install_hook_bash_security_review(tmp_path: Path) -> None:
    """--hook bash-security-review installs a Bash-matcher hook."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run(
        "hooks", "install", "--hook", "bash-security-review", "--target", str(tgt), "--apply"
    )
    assert result.returncode == 0, result.stderr
    written = json.loads(tgt.read_text())
    all_cmds = set()
    for blocks in written.get("hooks", {}).values():
        for block in blocks:
            for h in block.get("hooks", []):
                all_cmds.add(h.get("command", ""))
    assert "ccst hooks run bash-security-review" in all_cmds


def test_hooks_install_explicit_source_still_works(tmp_path: Path) -> None:
    """--source still works as before for advanced users."""
    src = tmp_path / "custom.json"
    tgt = tmp_path / "target.json"
    src.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "my-custom-hook"}]}]}
    }))
    _write(tgt, {})
    result = _run("hooks", "install", "--source", str(src), "--target", str(tgt), "--apply")
    assert result.returncode == 0, result.stderr
    written = json.loads(tgt.read_text())
    cmds = [
        h["command"]
        for blocks in written.get("hooks", {}).values()
        for block in blocks
        for h in block.get("hooks", [])
    ]
    assert "my-custom-hook" in cmds


# ---------- bundle JSON shape ----------

def test_bundle_json_has_correct_events() -> None:
    """The hooks-bundle.json contains the expected event types."""
    bundle_path = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    assert bundle_path.is_file(), f"Bundle not found: {bundle_path}"
    bundle = json.loads(bundle_path.read_text())
    events = set(bundle["hooks"].keys())
    assert events == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}


def test_bundle_json_bash_security_review_has_bash_matcher() -> None:
    bundle_path = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    bundle = json.loads(bundle_path.read_text())
    found = False
    for block in bundle["hooks"]["PreToolUse"]:
        cmds = [h["command"] for h in block.get("hooks", [])]
        if "ccst hooks run bash-security-review" in cmds:
            assert block.get("matcher") == "Bash"
            found = True
    assert found, "bash-security-review not found in PreToolUse"


def test_bundle_json_edit_write_audit_has_matcher() -> None:
    bundle_path = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    bundle = json.loads(bundle_path.read_text())
    found = False
    for block in bundle["hooks"]["PostToolUse"]:
        cmds = [h["command"] for h in block.get("hooks", [])]
        if "ccst hooks run edit-write-audit" in cmds:
            assert "matcher" in block
            found = True
    assert found, "edit-write-audit not found in PostToolUse"


def test_bundle_json_confirm_8digit_has_no_matcher() -> None:
    bundle_path = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    bundle = json.loads(bundle_path.read_text())
    found = False
    for block in bundle["hooks"]["PreToolUse"]:
        cmds = [h["command"] for h in block.get("hooks", [])]
        if "ccst hooks run confirm-8digit" in cmds:
            assert "matcher" not in block
            found = True
    assert found, "confirm-8digit not found in PreToolUse"


# ---------- table output ----------

ALL_HOOK_NAMES = (
    "bash-security-review",
    "confirm-8digit",
    "edit-write-audit",
    "prompt-guard",
    "session-end",
    "session-tag",
)


def test_hooks_install_table_has_expected_headers(tmp_path: Path) -> None:
    """The table has Hook/Status/Event/Description column headers."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--target", str(tgt))
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for header in ("Hook", "Status", "Event", "Description"):
        assert header in out, f"missing header {header!r} in:\n{out}"


def test_hooks_install_table_lists_all_bundled_hooks(tmp_path: Path) -> None:
    """The dry-run table includes a row for every bundled hook."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--target", str(tgt))
    assert result.returncode == 0, result.stderr
    for name in ALL_HOOK_NAMES:
        assert name in result.stdout, f"missing hook {name!r} in:\n{result.stdout}"


def test_hooks_install_table_shows_install_status_when_new(tmp_path: Path) -> None:
    """An empty target shows status 'install' for every bundled hook."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--target", str(tgt))
    assert result.returncode == 0
    # Every hook line should have 'install' status (not already-installed)
    for name in ALL_HOOK_NAMES:
        # find the row, assert it contains 'install' but not 'already-installed'
        line = next((ln for ln in result.stdout.splitlines() if ln.startswith(name)), None)
        assert line is not None, f"no row for {name}"
        assert "install" in line
        assert "already-installed" not in line


def test_hooks_install_table_shows_already_installed_after_apply(tmp_path: Path) -> None:
    """After --apply, re-running shows status 'already-installed' for each hook."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    _run("hooks", "install", "--target", str(tgt), "--apply")
    result = _run("hooks", "install", "--target", str(tgt))
    assert result.returncode == 0
    for name in ALL_HOOK_NAMES:
        line = next((ln for ln in result.stdout.splitlines() if ln.startswith(name)), None)
        assert line is not None, f"no row for {name}"
        assert "already-installed" in line


def test_hooks_install_table_shows_event_names(tmp_path: Path) -> None:
    """The table shows the CC event name for each hook (incl. matcher in brackets)."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--target", str(tgt))
    out = result.stdout
    assert "PreToolUse[Bash]" in out  # bash-security-review's matcher
    assert "SessionStart" in out
    assert "UserPromptSubmit" in out
    assert "PostToolUse[Edit|Write|NotebookEdit]" in out
    assert "Stop" in out


def test_hooks_install_table_shows_descriptions(tmp_path: Path) -> None:
    """The table shows a non-empty description for every bundled hook."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--target", str(tgt))
    # Spot-check that the description column carries meaningful text per hook
    assert "shell commands" in result.stdout  # bash-security-review
    assert "8-digit confirmation" in result.stdout
    assert "credential" in result.stdout  # prompt-guard
    assert "WORKLOG" in result.stdout  # session-end
    assert "session tag" in result.stdout
    assert "Edit/Write" in result.stdout  # edit-write-audit


def test_hooks_install_hook_selector_table_only_named_row(tmp_path: Path) -> None:
    """--hook X shows a table with only that one hook listed."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "install", "--hook", "session-tag", "--target", str(tgt))
    assert result.returncode == 0
    # session-tag should be on a row; other hooks should not have rows
    assert "session-tag" in result.stdout
    for other in [n for n in ALL_HOOK_NAMES if n != "session-tag"]:
        # An 'other' hook name should not appear at the start of any line in the table
        rows = [ln for ln in result.stdout.splitlines() if ln.startswith(other)]
        assert not rows, f"unexpected row for {other!r} in:\n{result.stdout}"


def test_hooks_install_table_mixed_status_after_partial_install(tmp_path: Path) -> None:
    """After installing only session-tag, the full-bundle table shows mixed statuses."""
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    _run("hooks", "install", "--hook", "session-tag", "--target", str(tgt), "--apply")
    result = _run("hooks", "install", "--target", str(tgt))
    assert result.returncode == 0
    # session-tag is already there
    line_st = next(ln for ln in result.stdout.splitlines() if ln.startswith("session-tag"))
    assert "already-installed" in line_st
    # session-end is still new
    line_se = next(ln for ln in result.stdout.splitlines() if ln.startswith("session-end"))
    assert "install" in line_se
    assert "already-installed" not in line_se
