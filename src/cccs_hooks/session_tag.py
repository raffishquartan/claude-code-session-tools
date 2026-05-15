"""SessionStart hook: write <session_id>.tag into the CC projects directory.

When CLD_SESSION_TAG is set (i.e. the session was started via ccd), writes a
small tag file so that ccusage and other tools can map session UUIDs to the
human-readable name tag assigned at session creation.

File written:  ~/.claude/projects/<encoded-cwd>/<session_id>.tag
File content:  the session name tag, e.g. "oneshot-add-uuid-for-better-usage-mapping\n"

Path encoding: Claude Code encodes the project cwd as the directory name under
~/.claude/projects/ by replacing every non-alphanumeric character with '-'.
This module uses the same encoding so the tag file lands in the correct directory.

Runs silently when CLD_SESSION_TAG is not set (non-ccd sessions).
Never raises — write failures are reported to stderr only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def encode_path(path: str) -> str:
    """Encode a filesystem path to the name Claude Code uses under ~/.claude/projects/.

    Claude Code replaces every character that is not alphanumeric with '-'.
    Examples:
        /home/alice          -> -home-chris
        /home/alice/.claude  -> -home-chris--claude   (the '.' also becomes '-')
        /mnt/c/Users/alice/OneDrive/claude/oneshot
                             -> -mnt-c-Users-cfoge-OneDrive-claude-oneshot
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def main(argv: list[str] | None = None) -> int:
    tag = os.environ.get("CLD_SESSION_TAG")
    if not tag:
        return 0

    raw = sys.stdin.read()
    try:
        data: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[session-tag] Could not parse hook payload: {exc}", file=sys.stderr)
        return 0

    session_id = str(data.get("session_id") or "")
    if not session_id:
        print(
            f"[session-tag] session_id absent from hook payload for tag {tag!r}; "
            "tag file not written",
            file=sys.stderr,
        )
        return 0

    cwd = str(
        data.get("cwd")
        or os.environ.get("CLAUDE_PROJECT_DIR")
        or os.getcwd()
    )

    try:
        target_dir = DEFAULT_PROJECTS_DIR / encode_path(cwd)
        target_dir.mkdir(parents=True, exist_ok=True)
        tag_file = target_dir / f"{session_id}.tag"
        tag_file.write_text(tag + "\n")
    except OSError as exc:
        print(f"[session-tag] Failed to write tag file: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
