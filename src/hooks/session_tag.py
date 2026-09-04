"""SessionStart hook: records the session tag + .last-opened activity into
sessions.db, and emits ccd/ccr session context.

When CLD_SESSION_TAG is set (i.e. the session was started via the `ccd` or
`ccr` shell wrapper), this hook does two things:

1. Records session_id -> tag in sessions.db's session_tags table, so ccs/ccr
   and other tools can map session UUIDs to the human-readable name tag
   assigned at session creation.

2. If CLD_SESSION_DIR is set and shaped like <project_dir>/cc-sessions/<basename>,
   upserts the sessions table's last_opened timestamp for that row (creating
   the row if it does not already exist — see sessions_db.touch_last_opened).
   If CLD_SESSION_DIR is shaped like cc-sessions/<basename> but not absolute,
   the write is skipped (it would collapse project_dir to '.') and the
   anomaly is surfaced via both additionalContext and systemMessage.

3. Emits `additionalContext` (mode-specific for CLD_SESSION_MODE=new vs
   resume) telling the assistant the tag/session-dir is already set, so it
   skips asking the user for a session name.

Runs silently (returns 0, emits nothing) when CLD_SESSION_TAG is not set
(non-ccd/ccr sessions). Never raises — write failures are reported to
stderr only and do not prevent the additionalContext from being emitted.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

from cc_session_tools.lib import sessions_db


def encode_path(path: str) -> str:
    """Encode a filesystem path to the name Claude Code uses under ~/.claude/projects/.

    Claude Code replaces every character that is not alphanumeric with '-'.
    Examples:
        /home/alice          -> -home-alice
        /home/alice/.claude  -> -home-alice--claude   (the '.' also becomes '-')
        /mnt/c/Users/alice/repos/myproject
                             -> -mnt-c-Users-alice-repos-myproject

    NOTE: encode_path() is not used for tag recording (tags are now uuid-keyed
    rows in sessions.db, not cwd-encoded paths). It is kept because its
    documented contract is tested and removing it is a separate cleanup.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def _additional_context_message(tag: str, session_dir: str, mode: str) -> str:
    """Build the mode-specific SessionStart additionalContext message.

    Ported verbatim (content-wise) from the former cc-wrapper-session-tag.sh
    in claude-code-config-sync.
    """
    if mode == "resume":
        return (
            f"Session tag is already set to `{tag}` by the ccr shell wrapper. "
            "The session is being resumed today. The session directory "
            f"`{session_dir}/` already exists. Session names reflect the start "
            "date only and are not renamed just because activity spans multiple "
            "days. Do NOT ask the user for a name tag — skip that step in the "
            "CLAUDE.md startup flow. Proceed directly to the hooks report as normal."
        )
    return (
        f"Session tag is already set to `{tag}` by the ccd shell wrapper. "
        f"The session directory `{session_dir}/` (with working/ and out/ "
        "subdirs) has already been created. The session display name has "
        "already been set via `claude -n` at startup, so /rename is "
        "unnecessary. Do NOT ask the user for a name tag — skip that step in "
        "the CLAUDE.md startup flow. Proceed directly to the hooks report as normal."
    )


def main(argv: list[str] | None = None) -> int:
    tag = os.environ.get("CLD_SESSION_TAG")
    if not tag:
        return 0

    raw = sys.stdin.read()
    try:
        data: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[session-tag] Could not parse hook payload: {exc}", file=sys.stderr)
        data = {}

    session_id = str(data.get("session_id") or "")
    if not session_id:
        print(
            f"[session-tag] session_id absent from hook payload for tag {tag!r}; "
            "tag not recorded",
            file=sys.stderr,
        )
    else:
        try:
            sessions_db.write_tag(session_id, tag)
        except (OSError, sqlite3.Error) as exc:
            print(f"[session-tag] Failed to record tag: {exc}", file=sys.stderr)

    session_dir_str = os.environ.get("CLD_SESSION_DIR", "")
    dir_rejected_msg: str | None = None
    if session_dir_str:
        session_dir_path = Path(session_dir_str)
        if session_dir_path.parent.name == "cc-sessions":
            if not session_dir_path.is_absolute():
                dir_rejected_msg = (
                    f"CLD_SESSION_DIR is not absolute ({session_dir_str!r}); skipping "
                    "sessions.db update to avoid writing a corrupt project_dir. Run "
                    "'ccst repair sessions --dry-run' if ccl/ccs --global later looks "
                    "incomplete."
                )
            else:
                try:
                    sessions_db.touch_last_opened(
                        session_dir_path.parent.parent, session_dir_path.name
                    )
                except (OSError, sqlite3.Error) as exc:
                    print(f"[session-tag] Failed to record .last-opened: {exc}", file=sys.stderr)

    session_dir = session_dir_str or f"cc-sessions/{date.today():%Y%m%d}-{tag}"
    mode = os.environ.get("CLD_SESSION_MODE", "new")
    context = _additional_context_message(tag, session_dir, mode)
    output: dict[str, object] = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                context if dir_rejected_msg is None
                else f"{context}\n\n[session-tag] {dir_rejected_msg}"
            ),
        }
    }
    if dir_rejected_msg is not None:
        output["systemMessage"] = f"[session-tag] {dir_rejected_msg}"
    print(json.dumps(output))

    return 0


if __name__ == "__main__":
    sys.exit(main())
