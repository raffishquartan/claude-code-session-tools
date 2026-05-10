"""Parse Claude Code session JSONL files into a tidy fact table.

Each `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` file is a stream of
records of mixed types. This module:

1. Skips non-billable records (anything that is not an assistant message
   with a usage block).
2. Validates the shape of each billable record against the expected
   schema and reports drift via warnings.
3. Normalises each billable record into a single tidy row with
   consistent column names suitable for pandas / parquet.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator

from . import attribution, parent_inference, schema


_HARNESS_PREFIXES = ("<local-command-", "<command-name>", "<local-command-stdout>")
_HOOK_PREFIX = "Review this shell command for security risks"
_PROMPT_FILE_PREFIXES = ("Here is your prompt", "Your prompt: ")
_METADATA_SCAN_LINES = 500


log = logging.getLogger(__name__)


def parse_session_metadata(path: str | Path) -> dict[str, Any]:
    """Scan a JSONL file for session-level metadata (non-billable records).

    Returns a dict with keys:
      custom_title:    str | None  — from the first type=="custom-title" record
      is_sidechain:    bool        — True if any record has isSidechain==True
      initiation_type: str         — "hook-security-review" | "prompt-file" |
                                     "interactive" | "unknown"
      first_prompt:    str | None  — first 80 chars of first real user text
    """
    result: dict[str, Any] = {
        "custom_title": None,
        "is_sidechain": False,
        "initiation_type": "unknown",
        "first_prompt": None,
    }
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i >= _METADATA_SCAN_LINES:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rtype = record.get("type")
                if rtype == "custom-title" and result["custom_title"] is None:
                    title = record.get("customTitle")
                    if title:
                        result["custom_title"] = title
                if record.get("isSidechain") is True:
                    result["is_sidechain"] = True
                if rtype == "user" and result["initiation_type"] == "unknown":
                    text = _extract_user_text(record)
                    if text is not None:
                        result["first_prompt"] = text[:80]
                        if text.startswith(_HOOK_PREFIX):
                            result["initiation_type"] = "hook-security-review"
                        elif text.startswith(_PROMPT_FILE_PREFIXES):
                            result["initiation_type"] = "prompt-file"
                        else:
                            result["initiation_type"] = "interactive"
    except OSError:
        pass
    return result


def _extract_user_text(record: dict[str, Any]) -> str | None:
    """Return the first real user text from a user record, or None if harness-injected."""
    content = record.get("message", {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = next(
            (block.get("text", "") for block in content if block.get("type") == "text"),
            None,
        )
        if text is None:
            return None
    else:
        return None
    if not text or text.startswith(_HARNESS_PREFIXES):
        return None
    return text


def parse_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Normalise one JSONL record. Returns None for non-billable records.

    Raises `schema.SchemaError` if the record is billable but malformed.
    """
    if not schema.is_billable_record(record):
        return None
    schema.assert_billable(record)
    message = record["message"]
    usage = message["usage"]
    cache_creation = usage.get("cache_creation") or {}
    server_tool_use = usage.get("server_tool_use") or {}
    cwd = record["cwd"]
    return {
        "ts": record["timestamp"],
        "session_id": record["sessionId"],
        "message_id": message.get("id", ""),
        "request_id": record.get("requestId", ""),
        "project_cwd": cwd,
        "project_name": os.path.basename(cwd.rstrip("/")) or cwd,
        "git_branch": record.get("gitBranch", ""),
        "model": message["model"],
        "service_tier": usage.get("service_tier", ""),
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "cache_creation_5m": int(cache_creation.get("ephemeral_5m_input_tokens", 0) or 0),
        "cache_creation_1h": int(cache_creation.get("ephemeral_1h_input_tokens", 0) or 0),
        "cache_read": int(usage.get("cache_read_input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "web_search_count": int(server_tool_use.get("web_search_requests", 0) or 0),
        "web_fetch_count": int(server_tool_use.get("web_fetch_requests", 0) or 0),
        "tool_calls": attribution.extract_tool_uses(message),
        "uuid": record["uuid"],
        "version": record.get("version", ""),
    }


def _classify_file(path: Path) -> tuple[str, str | None]:
    """Return (session_type, hook_parent_name) for a JSONL file.

    Reads only as far as needed: subagent detection is path-only; hook
    detection stops at the first user record.
    """
    if path.parent.name == "subagents" and path.stem.startswith("agent-"):
        return "subagent", None
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") == "user":
                content = record.get("message", {}).get("content", "")
                if isinstance(content, str) and (
                    content.startswith(parent_inference._HOOK_PROMPT)
                    or parent_inference._PREFIX_RE.match(content)
                ):
                    return "hook", parent_inference.extract_hook_parent_name(content)
                return "regular", None
    return "regular", None


def parse_file(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield one normalised row per billable record in a JSONL file.

    Malformed JSON lines and blank lines are skipped with a warning.
    Non-billable records are silently skipped.
    """
    p = Path(path)
    session_type, hook_parent_name = _classify_file(p)
    with p.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("malformed JSON in %s line %d: %s", p, line_no, exc)
                continue
            row = parse_record(record)
            if row is not None:
                row["source_file"] = str(p)
                row["session_type"] = session_type
                row["hook_parent_name"] = hook_parent_name
                yield row


def parse_directory(root: str | Path) -> Iterator[dict[str, Any]]:
    """Yield rows from every `*.jsonl` file under `root`."""
    root = Path(root)
    for path in sorted(root.rglob("*.jsonl")):
        yield from parse_file(path)
