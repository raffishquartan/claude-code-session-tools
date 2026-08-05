"""Merge Claude Code hook entries from a source settings.json into a target,
and prune entries naming a hook this build no longer has.

The merge is additive by design — it must never disturb hook entries CCST
does not own. The prune is the matching subtractive pass, and it is not
optional housekeeping: a settings.json entry pointing at a removed hook makes
Claude Code run `ccst hooks run <gone>`, which exits non-zero on every single
event it is registered for. Without the prune, every hook removal or rename
strands the old entry in place forever, because nothing else ever rewrites
that file.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cc_session_tools.lib.hook_registry import HOOK_VERBS, hook_name_from_command


@dataclass(frozen=True)
class Addition:
    event: str
    matcher: str | None
    command: str


@dataclass(frozen=True)
class Removal:
    event: str
    matcher: str | None
    command: str
    hook_name: str


def _command_set(hooks: list[dict[str, Any]]) -> set[str]:
    return {h["command"] for h in hooks if h.get("type") == "command"}


def _find_block(blocks: list[dict[str, Any]], matcher: str | None) -> dict[str, Any] | None:
    for block in blocks:
        if block.get("matcher") == matcher:
            return block
    return None


def merge_hook_settings(
    source_settings: dict[str, Any],
    target_settings: dict[str, Any],
) -> tuple[dict[str, Any], list[Addition]]:
    """Return (merged_settings, additions) without modifying either input."""
    result = copy.deepcopy(target_settings)
    additions: list[Addition] = []

    source_hooks = source_settings.get("hooks", {})
    if not source_hooks:
        return result, additions

    target_hooks = result.setdefault("hooks", {})

    for event, source_blocks in source_hooks.items():
        target_blocks = target_hooks.setdefault(event, [])
        for source_block in source_blocks:
            matcher = source_block.get("matcher")
            target_block = _find_block(target_blocks, matcher)
            if target_block is None:
                target_block = {"hooks": []}
                if matcher is not None:
                    target_block["matcher"] = matcher
                target_blocks.append(target_block)
            existing_cmds = _command_set(target_block["hooks"])
            for hook in source_block.get("hooks", []):
                cmd = hook.get("command", "")
                if cmd not in existing_cmds:
                    target_block["hooks"].append(copy.deepcopy(hook))
                    existing_cmds.add(cmd)
                    additions.append(Addition(event=event, matcher=matcher, command=cmd))

    return result, additions


def prune_stale_hooks(
    target_settings: dict[str, Any],
    known_hooks: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[Removal]]:
    """Return (pruned_settings, removals) without modifying the input.

    Removes every ``ccst hooks run <name>`` entry whose ``<name>`` is absent
    from ``known_hooks`` (default: HOOK_VERBS). Entries whose command does not
    carry that prefix belong to someone else and are left untouched, as are
    empty blocks/events that contained only foreign entries — the prune only
    collapses containers it emptied itself.
    """
    verbs = HOOK_VERBS if known_hooks is None else known_hooks
    result = copy.deepcopy(target_settings)
    removals: list[Removal] = []

    hooks_section = result.get("hooks", {})
    emptied_events: list[str] = []

    for event, blocks in hooks_section.items():
        emptied_blocks: list[int] = []
        for block_idx, block in enumerate(blocks):
            matcher = block.get("matcher")
            kept: list[dict[str, Any]] = []
            for entry in block.get("hooks", []):
                command = entry.get("command", "")
                name = hook_name_from_command(command)
                if name is not None and name not in verbs:
                    removals.append(
                        Removal(event=event, matcher=matcher, command=command, hook_name=name)
                    )
                else:
                    kept.append(entry)
            if len(kept) != len(block.get("hooks", [])):
                block["hooks"] = kept
                if not kept:
                    emptied_blocks.append(block_idx)
        for idx in reversed(emptied_blocks):
            blocks.pop(idx)
        if not blocks and any(r.event == event for r in removals):
            emptied_events.append(event)

    for event in emptied_events:
        del hooks_section[event]

    if not hooks_section and "hooks" in result and removals:
        del result["hooks"]

    return result, removals


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)  # type: ignore[no-any-return]


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
