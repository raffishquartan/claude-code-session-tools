"""Tests for ccst hooks uninstall."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.cli.ccst import _remove_hooks


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


def _settings_with_hooks(*commands: str, event: str = "Stop", matcher: str | None = None) -> dict:
    block: dict = {"hooks": [{"type": "command", "command": c} for c in commands]}
    if matcher is not None:
        block["matcher"] = matcher
    return {"hooks": {event: [block]}}


# ---------- _remove_hooks unit tests ----------

def test_remove_all_ccst_hooks_from_settings() -> None:
    settings = _settings_with_hooks("ccst hooks run after-response")
    removed: list = []
    new = _remove_hooks(settings, None, removed)
    assert removed == [("Stop", None, "ccst hooks run after-response")]
    assert "hooks" not in new  # empty section cleaned up


def test_remove_named_hook_only() -> None:
    settings = _settings_with_hooks(
        "ccst hooks run after-response",
        "ccst hooks run session-tag",
        event="Stop",
    )
    removed: list = []
    new = _remove_hooks(settings, "after-response", removed)
    cmds = [r[2] for r in removed]
    assert "ccst hooks run after-response" in cmds
    assert "ccst hooks run session-tag" not in cmds
    # session-tag still present
    remaining_cmds = [
        h["command"]
        for blocks in new.get("hooks", {}).values()
        for block in blocks
        for h in block.get("hooks", [])
    ]
    assert "ccst hooks run session-tag" in remaining_cmds


def test_remove_leaves_non_ccst_hooks_intact() -> None:
    settings: dict = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {"type": "command", "command": "ccst hooks run after-response"},
                        {"type": "command", "command": "my-other-hook"},
                    ]
                }
            ]
        }
    }
    removed: list = []
    new = _remove_hooks(settings, None, removed)
    cmds = [r[2] for r in removed]
    assert "ccst hooks run after-response" in cmds
    # my-other-hook must be kept
    remaining_cmds = [
        h["command"]
        for blocks in new.get("hooks", {}).values()
        for block in blocks
        for h in block.get("hooks", [])
    ]
    assert "my-other-hook" in remaining_cmds


def test_remove_with_matcher_preserved_in_report() -> None:
    settings = _settings_with_hooks(
        "ccst hooks run bash-security-review", event="PreToolUse", matcher="Bash"
    )
    removed: list = []
    _remove_hooks(settings, None, removed)
    assert removed == [("PreToolUse", "Bash", "ccst hooks run bash-security-review")]


def test_remove_cleans_empty_event_section() -> None:
    settings = _settings_with_hooks("ccst hooks run after-response")
    removed: list = []
    new = _remove_hooks(settings, None, removed)
    assert "Stop" not in new.get("hooks", {})


def test_remove_does_not_mutate_input() -> None:
    settings = _settings_with_hooks("ccst hooks run after-response")
    original = json.dumps(settings, sort_keys=True)
    removed: list = []
    _remove_hooks(settings, None, removed)
    assert json.dumps(settings, sort_keys=True) == original


def test_remove_no_match_returns_empty_removed_list() -> None:
    settings = _settings_with_hooks("my-hook")
    removed: list = []
    _remove_hooks(settings, None, removed)
    assert removed == []


# ---------- CLI integration tests ----------

def test_hooks_uninstall_dry_run_default(tmp_path: Path) -> None:
    tgt = tmp_path / "settings.json"
    _write(tgt, _settings_with_hooks("ccst hooks run after-response"))
    result = _run("hooks", "uninstall", "--target", str(tgt))
    assert result.returncode == 0, result.stderr
    assert "Dry run" in result.stdout
    # Not written
    assert json.loads(tgt.read_text()) != {}


def test_hooks_uninstall_apply_removes_entry(tmp_path: Path) -> None:
    tgt = tmp_path / "settings.json"
    _write(tgt, _settings_with_hooks("ccst hooks run after-response"))
    result = _run("hooks", "uninstall", "--target", str(tgt), "--apply")
    assert result.returncode == 0, result.stderr
    written = json.loads(tgt.read_text())
    assert "hooks" not in written or "Stop" not in written.get("hooks", {})


def test_hooks_uninstall_named_hook(tmp_path: Path) -> None:
    tgt = tmp_path / "settings.json"
    _write(
        tgt,
        {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {"type": "command", "command": "ccst hooks run after-response"},
                            {"type": "command", "command": "ccst hooks run session-tag"},
                        ]
                    }
                ]
            }
        },
    )
    result = _run("hooks", "uninstall", "--hook", "after-response", "--target", str(tgt), "--apply")
    assert result.returncode == 0, result.stderr
    written = json.loads(tgt.read_text())
    remaining = [
        h["command"]
        for blocks in written.get("hooks", {}).values()
        for block in blocks
        for h in block.get("hooks", [])
    ]
    assert "ccst hooks run after-response" not in remaining
    assert "ccst hooks run session-tag" in remaining


def test_hooks_uninstall_nothing_to_remove(tmp_path: Path) -> None:
    tgt = tmp_path / "settings.json"
    _write(tgt, {})
    result = _run("hooks", "uninstall", "--target", str(tgt))
    assert result.returncode == 0
    assert "not found" in result.stdout.lower() or "nothing" in result.stdout.lower()


def test_hooks_uninstall_missing_settings_exits_gracefully(tmp_path: Path) -> None:
    tgt = tmp_path / "missing.json"
    result = _run("hooks", "uninstall", "--target", str(tgt))
    assert result.returncode == 0
    assert "nothing" in result.stdout.lower() or "no settings" in result.stdout.lower()


def test_hooks_uninstall_shows_removed_entries(tmp_path: Path) -> None:
    tgt = tmp_path / "settings.json"
    _write(tgt, _settings_with_hooks("ccst hooks run after-response"))
    result = _run("hooks", "uninstall", "--target", str(tgt))
    assert result.returncode == 0, result.stderr
    assert "after-response" in result.stdout


def test_hooks_uninstall_preserves_non_ccst_hooks(tmp_path: Path) -> None:
    tgt = tmp_path / "settings.json"
    _write(
        tgt,
        {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {"type": "command", "command": "ccst hooks run after-response"},
                            {"type": "command", "command": "my-other-hook"},
                        ]
                    }
                ]
            }
        },
    )
    result = _run("hooks", "uninstall", "--target", str(tgt), "--apply")
    assert result.returncode == 0, result.stderr
    written = json.loads(tgt.read_text())
    remaining = [
        h["command"]
        for blocks in written.get("hooks", {}).values()
        for block in blocks
        for h in block.get("hooks", [])
    ]
    assert "my-other-hook" in remaining
    assert "ccst hooks run after-response" not in remaining
