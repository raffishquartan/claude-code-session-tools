"""Resolve Claude Code session UUIDs to their human-readable display names.

Display names are set via `claude -n <name>` (or by `ccd <tag>` which
calls it under the hood). The Claude Code CLI writes them into
`~/.claude/sessions/<pid>.json` while the session is live, but those
files are pruned once the OS process exits, so the live registry is
ephemeral.

This module captures names while they exist and persists them to a
small on-disk cache (default
`<cache_dir>/session_names.json`) keyed by session UUID. Every query
run merges the live registry into the cache before resolving names, so
any name we have ever seen is preserved indefinitely.

Public API:

    load_live_names(sessions_dir) -> dict[uuid, name]
    update_persistent_cache(cache_path, sessions_dir=None) -> dict[uuid, name]
    fallback_name(uuid) -> str       # "sess-ddab284a" for unknown UUIDs
    format_session(uuid, name_map, fmt) -> str  # "name", "uuid", "both"
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from . import parser as _parser

log = logging.getLogger(__name__)

DEFAULT_LIVE_DIR = Path.home() / ".claude" / "sessions"


def load_live_names(sessions_dir: Path | str | None = None) -> dict[str, str]:
    """Return {session_uuid: display_name} from ~/.claude/sessions/*.json."""
    base = Path(sessions_dir) if sessions_dir is not None else DEFAULT_LIVE_DIR
    out: dict[str, str] = {}
    if not base.is_dir():
        return out
    for path in base.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.debug("skipping unreadable session file %s: %s", path, e)
            continue
        sid = data.get("sessionId")
        name = data.get("name")
        if sid and name:
            out[sid] = name
    return out


def _read_cache(cache_path: Path) -> dict[str, str]:
    if not cache_path.is_file():
        return {}
    try:
        return json.loads(cache_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning("ignoring corrupt session-name cache %s: %s", cache_path, e)
        return {}


def _write_cache(cache_path: Path, data: dict[str, str]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, indent=2))
    tmp.replace(cache_path)


def load_jsonl_titles(projects_dir: Path | str) -> dict[str, str]:
    """Return {session_uuid: custom_title} from JSONL files under `projects_dir`.

    Iterates every *.jsonl under the directory and extracts the first
    custom-title record from each via parse_session_metadata(). Files without
    a custom-title are skipped.
    """
    base = Path(projects_dir)
    if not base.is_dir():
        return {}
    out: dict[str, str] = {}
    for path in base.rglob("*.jsonl"):
        meta = _parser.parse_session_metadata(path)
        title = meta.get("custom_title")
        sid = meta.get("session_id")
        if title and sid:
            out[sid] = title
    return out


def update_persistent_cache(
    cache_path: Path | str,
    sessions_dir: Path | str | None = None,
    projects_dir: Path | str | None = None,
) -> dict[str, str]:
    """Merge live names and JSONL custom-titles into the on-disk cache.

    Priority (highest first):
      1. Live PID-file names (set by `claude -n`, pruned on process exit)
      2. JSONL custom-title records (written when user runs /rename)
      3. Existing cached name
      4. Falls back to sess-<uuid8> at display time (not stored here)

    `projects_dir` is `~/.claude/projects` (None = skip JSONL scan).
    """
    cache_path = Path(cache_path)
    merged = _read_cache(cache_path)
    if projects_dir is not None:
        jsonl_titles = load_jsonl_titles(projects_dir)
        merged.update(jsonl_titles)  # JSONL beats cached stale names
    live = load_live_names(sessions_dir)
    if live:
        merged.update(live)  # live PID file always wins
    if live or (projects_dir is not None):
        _write_cache(cache_path, merged)
    return merged


def fallback_name(uuid: str) -> str:
    """Stable short identifier when no display name is known."""
    return f"sess-{uuid[:8]}"


def format_session(uuid: str, name_map: dict[str, str], fmt: str = "name") -> str:
    """Render a session identifier per `fmt`.

    fmt:
        "name"  -> display name, or fallback("sess-<uuid8>") if unknown.
        "uuid"  -> full UUID.
        "both"  -> "<name>  (<uuid>)" - name first so it sorts naturally.
    """
    name = name_map.get(uuid) or fallback_name(uuid)
    if fmt == "uuid":
        return uuid
    if fmt == "both":
        return f"{name}  ({uuid})"
    if fmt == "name":
        return name
    raise ValueError(f"unknown session format: {fmt!r}")


def resolve_filter(
    needle: str,
    name_map: dict[str, str],
) -> Iterable[str]:
    """Resolve a `--session` filter value to one or more UUIDs.

    Matches in priority order:
      1. exact UUID (returned as-is, even if not in the map)
      2. exact name match (case-sensitive)
      3. UUID prefix (>= 4 chars, single match required to be useful)
      4. case-insensitive substring match against names

    Returns the list of UUIDs to filter on. Empty list means no match.
    """
    if not needle:
        return []
    if needle in name_map.values():
        return [u for u, n in name_map.items() if n == needle]
    if needle in name_map:
        return [needle]
    # UUID prefix
    if len(needle) >= 4 and all(c in "0123456789abcdef-" for c in needle.lower()):
        prefix_hits = [u for u in name_map if u.lower().startswith(needle.lower())]
        if prefix_hits:
            return prefix_hits
        return [needle]  # let downstream filter handle exact-UUID-not-in-map
    # case-insensitive name substring
    n = needle.lower()
    return [u for u, name in name_map.items() if n in name.lower()]
