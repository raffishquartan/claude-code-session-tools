"""Merge Claude Code hook entries from a source settings.json into a target."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Addition:
    event: str
    matcher: str | None
    command: str


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


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)  # type: ignore[no-any-return]


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
