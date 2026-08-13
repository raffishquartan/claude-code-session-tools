"""End-to-end: `ccst hooks install` heals a settings.json stranded by an upgrade.

The scenario these cover is the one that actually happened. A settings.json
written at 0.16.0 registers edit-write-audit, prompt-guard and session-end.
0.17.0 deletes the first two and renames the third to after-response. Because
`hooks install` was additive-only and nothing else ever rewrote the file, the
three dead entries survived every subsequent upgrade — and Claude Code went on
running `ccst hooks run <gone>` on UserPromptSubmit, PostToolUse and Stop.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
BUNDLE = REPO_ROOT / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"

# Exactly what `git show v0.16.0:config/hooks-bundle.json` registered for the
# three hooks 0.17.0 went on to remove or rename.
_STRANDED_SETTINGS = {
    "hooks": {
        "SessionStart": [{"hooks": [
            {"type": "command", "command": "ccst hooks run session-tag", "timeout": 5},
        ]}],
        "UserPromptSubmit": [{"hooks": [
            {"type": "command", "command": "ccst hooks run prompt-guard", "timeout": 5},
        ]}],
        "PostToolUse": [{"matcher": "Edit|Write|NotebookEdit", "hooks": [
            {"type": "command", "command": "ccst hooks run edit-write-audit", "timeout": 10},
        ]}],
        "Stop": [{"hooks": [
            {"type": "command", "command": "ccst hooks run session-end", "timeout": 10},
        ]}],
    }
}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )


def _commands(settings: dict) -> set[str]:
    return {
        h.get("command", "")
        for blocks in settings.get("hooks", {}).values()
        for block in blocks
        for h in block.get("hooks", [])
    }


@pytest.fixture()
def stranded(tmp_path: Path) -> Path:
    target = tmp_path / "settings.json"
    target.write_text(json.dumps(_STRANDED_SETTINGS, indent=2))
    return target


def test_install_apply_removes_every_stranded_entry(stranded: Path) -> None:
    result = _run("hooks", "install", "--source", str(BUNDLE), "--target", str(stranded), "--apply")
    assert result.returncode == 0
    commands = _commands(json.loads(stranded.read_text()))
    assert "ccst hooks run prompt-guard" not in commands
    assert "ccst hooks run edit-write-audit" not in commands
    assert "ccst hooks run session-end" not in commands


def test_install_apply_adds_the_rename_target_in_the_same_pass(stranded: Path) -> None:
    """session-end -> after-response has to happen as one operation: leaving
    both registered would keep firing the dead one."""
    _run("hooks", "install", "--source", str(BUNDLE), "--target", str(stranded), "--apply")
    commands = _commands(json.loads(stranded.read_text()))
    assert "ccst hooks run after-response" in commands
    assert "ccst hooks run session-end" not in commands


def test_install_dry_run_reports_the_stale_entries_but_writes_nothing(stranded: Path) -> None:
    before = stranded.read_text()
    result = _run("hooks", "install", "--source", str(BUNDLE), "--target", str(stranded))
    assert result.returncode == 0
    assert "prompt-guard" in result.stdout
    assert "Dry run" in result.stdout
    assert stranded.read_text() == before


def test_install_leaves_hooks_ccst_does_not_own_alone(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "ccst hooks run session-end"},
            {"type": "command", "command": "/opt/mytool/notify.sh"},
        ]}]},
        "permissions": {"allow": ["Bash(ls:*)"]},
    }))
    _run("hooks", "install", "--source", str(BUNDLE), "--target", str(target), "--apply")
    settings = json.loads(target.read_text())
    assert "/opt/mytool/notify.sh" in _commands(settings)
    assert "ccst hooks run session-end" not in _commands(settings)
    assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}


def test_install_on_an_already_current_settings_changes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{}")
    _run("hooks", "install", "--source", str(BUNDLE), "--target", str(target), "--apply")
    first = target.read_text()

    result = _run("hooks", "install", "--source", str(BUNDLE), "--target", str(target), "--apply")
    assert "Already up to date" in result.stdout
    assert target.read_text() == first


def test_every_hook_left_in_settings_is_one_the_dispatcher_can_run(stranded: Path) -> None:
    """The property that matters: after an install, nothing in settings.json
    names a hook `ccst hooks run` would reject."""
    from cc_session_tools.lib.hook_registry import HOOK_VERBS, hook_name_from_command

    _run("hooks", "install", "--source", str(BUNDLE), "--target", str(stranded), "--apply")
    names = {hook_name_from_command(c) for c in _commands(json.loads(stranded.read_text()))}
    assert names - {None} <= set(HOOK_VERBS)
