# src/cc_session_tools/lib/messaging/tag_lookup.py
"""Resolve a session name-tag to one or more session UUIDs.

Tag files live at ``~/.cache/claude/session-tags/<uuid>.tag`` (written by the
SessionStart hook in cccs_hooks/session_tag.py).  The UUID in the filename
stem equals the ``sessionId`` in the Claude Code session registry at
``~/.claude/sessions/<pid>.json`` — verified at implementation time.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _session_tags_dir() -> Path:
    override = os.environ.get("CCCS_SESSION_TAGS_DIR")
    return Path(override) if override else Path.home() / ".cache" / "claude" / "session-tags"


def _sessions_dir() -> Path:
    return Path.home() / ".claude" / "sessions"


@dataclass(frozen=True)
class SessionTagMatch:
    uuid: str
    is_live: bool
    last_active: float | None  # epoch secs; best-effort ordering only


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, not ours
    except (OSError, ValueError):
        return False
    return True


def live_session_uuids() -> set[str]:
    """sessionId of every registry entry whose pid is a running process.
    Best-effort; never raises."""
    out: set[str] = set()
    d = _sessions_dir()
    if not d.is_dir():
        return out
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        pid, sid = data.get("pid"), data.get("sessionId")
        if isinstance(pid, int) and isinstance(sid, str) and _pid_alive(pid):
            out.add(sid)
    return out


def resolve_session_tag(tag: str) -> list[SessionTagMatch]:
    """All sessions whose .tag content == tag.
    Ranked most-recently-active first."""
    live = live_session_uuids()
    matches: list[SessionTagMatch] = []
    tags_dir = _session_tags_dir()
    if not tags_dir.is_dir():
        return matches
    for tag_file in tags_dir.glob("*.tag"):
        try:
            content = tag_file.read_text().strip()
        except OSError:
            continue
        if content != tag:
            continue
        uuid = tag_file.stem
        try:
            mtime: float | None = tag_file.stat().st_mtime
        except OSError:
            mtime = None
        matches.append(SessionTagMatch(uuid=uuid, is_live=uuid in live, last_active=mtime))
    matches.sort(key=lambda m: (m.last_active or 0.0), reverse=True)
    return matches
