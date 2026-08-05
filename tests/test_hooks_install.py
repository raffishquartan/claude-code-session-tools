"""Tests for cc_session_tools.hooks_install merge logic."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_session_tools.hooks_install import (
    Addition,
    Removal,
    merge_hook_settings,
    prune_stale_hooks,
    write_json_atomic,
)


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


# ---------- prune_stale_hooks ----------
#
# The three names below are the ones that actually stranded a settings.json:
# edit-write-audit and prompt-guard were deleted in 0.17.0 and session-end was
# renamed to after-response in the same release.

_GONE = ("edit-write-audit", "prompt-guard", "session-end")
_LIVE = ("session-tag", "after-response")


def _ccst(name: str) -> dict:
    return {"type": "command", "command": f"ccst hooks run {name}"}


def test_prune_removes_hooks_this_build_no_longer_has() -> None:
    target = {"hooks": {"UserPromptSubmit": [{"hooks": [_ccst("prompt-guard"), _ccst("session-tag")]}]}}
    pruned, removals = prune_stale_hooks(target)
    assert [r.hook_name for r in removals] == ["prompt-guard"]
    assert pruned["hooks"]["UserPromptSubmit"][0]["hooks"] == [_ccst("session-tag")]


@pytest.mark.parametrize("name", _GONE)
def test_prune_removes_each_hook_dropped_in_0_17_0(name: str) -> None:
    target = {"hooks": {"Stop": [{"hooks": [_ccst(name)]}]}}
    _, removals = prune_stale_hooks(target)
    assert [r.hook_name for r in removals] == [name]


@pytest.mark.parametrize("name", _LIVE)
def test_prune_keeps_hooks_this_build_still_has(name: str) -> None:
    target = {"hooks": {"SessionStart": [{"hooks": [_ccst(name)]}]}}
    pruned, removals = prune_stale_hooks(target)
    assert removals == []
    assert pruned == target


def test_prune_never_touches_hooks_ccst_does_not_own() -> None:
    """Anything not spelled `ccst hooks run <name>` belongs to someone else —
    a third-party hook, or one the operator wrote by hand."""
    target = {
        "hooks": {
            "Stop": [{"hooks": [
                {"type": "command", "command": "/opt/mytool/notify.sh"},
                {"type": "command", "command": "python3 -m my_hooks.audit"},
                _ccst("session-end"),
            ]}]
        }
    }
    pruned, removals = prune_stale_hooks(target)
    assert [r.hook_name for r in removals] == ["session-end"]
    assert [h["command"] for h in pruned["hooks"]["Stop"][0]["hooks"]] == [
        "/opt/mytool/notify.sh", "python3 -m my_hooks.audit",
    ]


def test_prune_drops_a_block_it_emptied() -> None:
    target = {
        "hooks": {
            "PostToolUse": [{"matcher": "Edit|Write", "hooks": [_ccst("edit-write-audit")]}],
            "SessionStart": [{"hooks": [_ccst("session-tag")]}],
        }
    }
    pruned, _ = prune_stale_hooks(target)
    assert "PostToolUse" not in pruned["hooks"]
    assert pruned["hooks"]["SessionStart"][0]["hooks"] == [_ccst("session-tag")]


def test_prune_leaves_an_empty_block_it_did_not_empty() -> None:
    target = {"hooks": {"Stop": [{"hooks": []}]}}
    pruned, removals = prune_stale_hooks(target)
    assert removals == []
    assert pruned == target


def test_prune_records_event_and_matcher_for_each_removal() -> None:
    target = {"hooks": {"PostToolUse": [{"matcher": "Edit|Write", "hooks": [_ccst("edit-write-audit")]}]}}
    _, removals = prune_stale_hooks(target)
    assert removals[0] == Removal(
        event="PostToolUse",
        matcher="Edit|Write",
        command="ccst hooks run edit-write-audit",
        hook_name="edit-write-audit",
    )


def test_prune_does_not_mutate_its_input() -> None:
    target = {"hooks": {"Stop": [{"hooks": [_ccst("session-end")]}]}}
    original = json.dumps(target, sort_keys=True)
    prune_stale_hooks(target)
    assert json.dumps(target, sort_keys=True) == original


def test_prune_of_a_clean_settings_is_a_no_op() -> None:
    target = {"hooks": {"SessionStart": [{"hooks": [_ccst("session-tag")]}]}, "permissions": {"allow": []}}
    pruned, removals = prune_stale_hooks(target)
    assert removals == []
    assert pruned == target


# ---------- write_json_atomic ----------

def test_write_json_atomic_creates_file(tmp_path: Path) -> None:
    dest = tmp_path / "settings.json"
    write_json_atomic(dest, {"hello": "world"})
    assert json.loads(dest.read_text()) == {"hello": "world"}


def test_write_json_atomic_no_tmp_left_behind(tmp_path: Path) -> None:
    dest = tmp_path / "settings.json"
    write_json_atomic(dest, {})
    assert not (tmp_path / "settings.tmp").exists()
