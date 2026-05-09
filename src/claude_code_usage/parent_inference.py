"""Infer parent/child relationships between Claude Code sessions.

Three session types:
- 'regular'  - normal interactive or agent sessions
- 'hook'     - bash-security-review spawned via `claude -p`; identified by
               first user message starting with the standard hook prompt
- 'subagent' - Task-tool spawned agents stored under
               <project>/<parent-uuid>/subagents/agent-<hash>.jsonl

Parent linkage:
- hook   -> parent name extracted from the session-prefix in the first user
            message (`<name>: Review this shell command...`) then resolved
            to a UUID via the session_names cache.  Falls back to None.
- subagent -> parent UUID extracted directly from the file path.
- regular  -> always None.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


_HOOK_PROMPT = "Review this shell command for security risks"
_PREFIX_RE = re.compile(r"^(\d{8}(?:-to-\d{8})?-[^:]+): " + re.escape(_HOOK_PROMPT))


def detect_session_type(path: Path | str, records: Iterable[dict[str, Any]]) -> str:
    """Return 'subagent', 'hook', or 'regular' for a JSONL session file.

    `records` is scanned only when path-based detection is inconclusive.
    """
    p = Path(path)
    if p.parent.name == "subagents" and p.stem.startswith("agent-"):
        return "subagent"
    for record in records:
        if record.get("type") == "user":
            content = record.get("message", {}).get("content", "")
            if isinstance(content, str) and (
                content.startswith(_HOOK_PROMPT) or _PREFIX_RE.match(content)
            ):
                return "hook"
            return "regular"
    return "regular"


def extract_hook_parent_name(content: str) -> str | None:
    """Extract the parent session name from a hook first-message, or None."""
    m = _PREFIX_RE.match(content)
    return m.group(1) if m else None


def _parent_uuid_from_subagent_path(source_file: str) -> str | None:
    """Extract the parent session UUID from a subagent file path.

    Expected structure: .../<encoded-cwd>/<parent-uuid>/subagents/agent-<hash>.jsonl
    Returns None if the path does not match the expected pattern.
    """
    parts = Path(source_file).parts
    # parts[-1] = agent-<hash>.jsonl
    # parts[-2] = 'subagents'
    # parts[-3] = <parent-uuid>
    if len(parts) >= 3 and parts[-2] == "subagents":
        return parts[-3]
    return None


def resolve_parents(df: pd.DataFrame, name_map: dict[str, str]) -> pd.DataFrame:
    """Add a `parent_session_id` column to `df`.

    `name_map` maps session UUID -> display name (as produced by
    `session_names.update_persistent_cache`). It is inverted internally
    to support name -> UUID lookups.

    Rules (applied per row):
    - session_type='subagent': parent UUID extracted from source_file path.
    - session_type='hook' + hook_parent_name present: parent UUID looked up
      via the inverted name_map.  Falls back to None.
    - Everything else: None.
    """
    name_to_uuid: dict[str, str] = {v: k for k, v in name_map.items()}

    def _infer(row: pd.Series) -> str | None:
        stype = row.get("session_type", "regular")
        if stype == "subagent":
            return _parent_uuid_from_subagent_path(str(row.get("source_file", "")))
        if stype == "hook":
            name = row.get("hook_parent_name")
            if name and not _is_na(name):
                return name_to_uuid.get(name)
        return None

    out = df.copy()
    out["parent_session_id"] = out.apply(_infer, axis=1)
    return out


def _is_na(value: Any) -> bool:
    try:
        return pd.isna(value)
    except (TypeError, ValueError):
        return False
