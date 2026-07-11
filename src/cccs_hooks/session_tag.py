"""SessionStart hook: write <session_id>.tag and emit ccd/ccr session context.

When CLD_SESSION_TAG is set (i.e. the session was started via the `ccd` or
`ccr` shell wrapper), this hook does two things:

1. Writes a small tag file so that ccusage and other tools can map session
   UUIDs to the human-readable name tag assigned at session creation.

   File written:  ~/.cache/claude/session-tags/<session_id>.tag
                  (overrideable via CCCS_SESSION_TAGS_DIR env var)
   File content:  the session name tag, e.g. "oneshot-add-uuid-for-better-usage-mapping\n"

   The flat layout (<dir>/<uuid>.tag) is viable because session IDs are
   globally unique UUIDs — no encoded-cwd subdirectory is needed.

2. Emits `additionalContext` (mode-specific for CLD_SESSION_MODE=new vs
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
import sys
from datetime import date
from pathlib import Path

DEFAULT_SESSION_TAGS_DIR: Path = Path.home() / ".cache" / "claude" / "session-tags"


def _session_tags_dir() -> Path:
    """Return the directory where .tag files are written.

    Overrideable via CCCS_SESSION_TAGS_DIR for testing and migration.
    """
    override = os.environ.get("CCCS_SESSION_TAGS_DIR")
    return Path(override) if override else DEFAULT_SESSION_TAGS_DIR


def encode_path(path: str) -> str:
    """Encode a filesystem path to the name Claude Code uses under ~/.claude/projects/.

    Claude Code replaces every character that is not alphanumeric with '-'.
    Examples:
        /home/alice          -> -home-alice
        /home/alice/.claude  -> -home-alice--claude   (the '.' also becomes '-')
        /mnt/c/Users/alice/repos/myproject
                             -> -mnt-c-Users-alice-repos-myproject

    NOTE: encode_path() is no longer used for tag-file writing (tag files now
    live in the flat ~/.cache/claude/session-tags/ directory keyed by UUID, not
    by project cwd). It is kept because its documented contract is tested and
    removing it is a separate cleanup commit.
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
            "tag file not written",
            file=sys.stderr,
        )
    else:
        try:
            tags_dir = _session_tags_dir()
            tags_dir.mkdir(parents=True, exist_ok=True)
            tag_file = tags_dir / f"{session_id}.tag"
            tag_file.write_text(tag + "\n")
        except OSError as exc:
            print(f"[session-tag] Failed to write tag file: {exc}", file=sys.stderr)

    session_dir_str = os.environ.get("CLD_SESSION_DIR", "")
    if session_dir_str:
        try:
            Path(session_dir_str).joinpath(".last-opened").touch()
        except OSError as exc:
            print(f"[session-tag] Failed to touch .last-opened: {exc}", file=sys.stderr)

    session_dir = session_dir_str or f"cc-sessions/{date.today():%Y%m%d}-{tag}"
    mode = os.environ.get("CLD_SESSION_MODE", "new")
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _additional_context_message(tag, session_dir, mode),
        }
    }))

    return 0


if __name__ == "__main__":
    sys.exit(main())
