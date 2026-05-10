"""Tests for cc_session_tools.hooks_install merge logic."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_session_tools.hooks_install import merge_hook_settings, Addition, write_json_atomic


def _cmd(name: str) -> dict:
    return {"type": "command", "command": name}


def _source(*commands: str, matcher: str | None = "Bash", event: str = "PreToolUse") -> dict:
    block: dict = {"hooks": [_cmd(c) for c in commands]}
    if matcher is not None:
        block["matcher"] = matcher
    return {"hooks": {event: [block]}}


# ---------- merge logic ----------

def test_empty_target_receives_all_source_hooks() -> None:
    source = _source("hook-a", "hook-b")
    merged, additions = merge_hook_settings(source_settings=source, target_settings={})
    assert merged["hooks"]["PreToolUse"][0]["hooks"] == [_cmd("hook-a"), _cmd("hook-b")]
    assert len(additions) == 2


def test_existing_commands_not_duplicated() -> None:
    source = _source("hook-a", "hook-b")
    target = _source("hook-a")
    merged, additions = merge_hook_settings(source_settings=source, target_settings=target)
    hooks = merged["hooks"]["PreToolUse"][0]["hooks"]
    assert hooks.count(_cmd("hook-a")) == 1
    assert _cmd("hook-b") in hooks
    assert len(additions) == 1
    assert additions[0].command == "hook-b"


def test_new_matcher_block_appended() -> None:
    source = _source("hook-a", matcher="Edit")
    target = _source("hook-x", matcher="Bash")
    merged, additions = merge_hook_settings(source_settings=source, target_settings=target)
    matchers = {b["matcher"] for b in merged["hooks"]["PreToolUse"]}
    assert matchers == {"Bash", "Edit"}
    assert len(additions) == 1


def test_new_event_type_added() -> None:
    source = _source("hook-a", event="Stop", matcher=None)
    merged, additions = merge_hook_settings(source_settings=source, target_settings={})
    assert "Stop" in merged["hooks"]
    assert len(additions) == 1


def test_no_matcher_block_merged_correctly() -> None:
    source = _source("new-hook", matcher=None, event="SessionStart")
    target = _source("existing-hook", matcher=None, event="SessionStart")
    merged, additions = merge_hook_settings(source_settings=source, target_settings=target)
    hooks = merged["hooks"]["SessionStart"][0]["hooks"]
    commands = [h["command"] for h in hooks]
    assert "existing-hook" in commands
    assert "new-hook" in commands
    assert len(additions) == 1


def test_source_without_hooks_key_is_no_op() -> None:
    target = _source("hook-x")
    merged, additions = merge_hook_settings(source_settings={}, target_settings=target)
    assert merged == target
    assert additions == []


def test_original_inputs_not_mutated() -> None:
    source = _source("hook-a", "hook-b")
    target = _source("hook-a")
    source_orig = json.dumps(source, sort_keys=True)
    target_orig = json.dumps(target, sort_keys=True)
    merge_hook_settings(source_settings=source, target_settings=target)
    assert json.dumps(source, sort_keys=True) == source_orig
    assert json.dumps(target, sort_keys=True) == target_orig


def test_addition_fields_populated() -> None:
    source = _source("hook-a", matcher="Bash", event="PreToolUse")
    merged, additions = merge_hook_settings(source_settings=source, target_settings={})
    assert additions[0] == Addition(event="PreToolUse", matcher="Bash", command="hook-a")


# ---------- write_json_atomic ----------

def test_write_json_atomic_creates_file(tmp_path: Path) -> None:
    dest = tmp_path / "settings.json"
    write_json_atomic(dest, {"hello": "world"})
    assert json.loads(dest.read_text()) == {"hello": "world"}


def test_write_json_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    dest = tmp_path / "settings.json"
    write_json_atomic(dest, {})
    assert not (tmp_path / "settings.tmp").exists()
