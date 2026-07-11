"""PreToolUse hook: auto-approve the ``touch`` that refreshes a skill marker.

Marker-gated skills (e.g. do-tesco-shop) keep a short-lived marker fresh under
``~/.cache/claude/markers/`` (see :mod:`cccs_hooks.markers`) by ``touch``-ing
it. That path is outside any project working directory, so without this hook
every refresh prompts for Bash permission. This hook returns a PreToolUse
``allow`` decision for *exactly* a bare ``touch <markers-dir>/<name>`` command
and nothing else, so marker refresh is silent while every other Bash call
falls through to the normal permission flow.

Security: the match is deliberately tight. The command must be a single ``touch``
with one path argument that resolves to a direct child of the markers directory
whose name is a plain filename. Any shell metacharacter (``| & ; < > ( ) $`` and
friends), any extra argument, any flag, or any path outside the markers
directory disqualifies the command - the hook then emits nothing and the call
proceeds to the normal permission prompt. A compound command such as
``touch <marker> && rm -rf ~`` is therefore never auto-approved.

The hook never denies and never blocks: it either emits an ``allow`` decision or
stays silent (exit 0). It cannot make any command *more* permissive than the
user's own settings already allow beyond the one narrow marker-touch case.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from cccs_hooks.markers import markers_dir

# Characters that introduce shell composition, expansion, redirection, quoting,
# or globbing. Their presence anywhere in the command disqualifies it - we only
# ever auto-approve a single literal touch with no shell machinery.
_FORBIDDEN_CHARS = set("|&;<>()$`\n\r\t*?[]{}!\"'\\=")

# A safe marker filename: letters, digits, dot, underscore, hyphen. No slashes,
# no whitespace, not "." or "..".
_SAFE_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def match_marker_touch(command: str, markers: Path) -> str | None:
    """Return the marker name if ``command`` is a bare ``touch`` of a direct
    child of ``markers``; otherwise ``None``.

    ``markers`` is passed explicitly (rather than read from
    :func:`cccs_hooks.markers.markers_dir`) so tests can point it at a temp dir.
    """
    stripped = command.strip()
    if not stripped:
        return None
    if any(ch in _FORBIDDEN_CHARS for ch in stripped):
        return None

    tokens = stripped.split()
    if len(tokens) != 2 or tokens[0] != "touch":
        return None

    path_token = tokens[1]
    if path_token.startswith("~/"):
        expanded = str(Path.home()) + path_token[1:]
    elif path_token.startswith("/"):
        expanded = path_token
    else:
        # Bare or relative names are ambiguous (depend on cwd); never match.
        return None

    norm = os.path.normpath(expanded)
    parent = os.path.dirname(norm)
    name = os.path.basename(norm)

    if parent != os.path.normpath(str(markers)):
        return None
    if name in (".", "..") or not _SAFE_NAME_RE.match(name):
        return None
    return name


def _emit_allow(name: str, markers: Path) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": (
                f"marker-allow: touch of skill marker '{name}' under {markers}"
            ),
        }
    }
    print(json.dumps(payload))


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(data, dict):
        return 0
    if str(data.get("tool_name", "")) != "Bash":
        return 0

    tool_input = data.get("tool_input")
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command", ""))

    markers = markers_dir()
    name = match_marker_touch(command, markers)
    if name is not None:
        _emit_allow(name, markers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
