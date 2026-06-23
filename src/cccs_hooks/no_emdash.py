"""Stop hook: detect and auto-correct em-dashes in assistant responses.

Fires via the Stop event after each Claude response. When the last
assistant turn contains an em-dash (U+2014, —), injects a targeted
correction prompt and returns exit code 2, which forces Claude to
restate only the affected sentence(s) with each em-dash replaced by a
space-surrounded hyphen ( - ).

Infinite-loop guard: if the most recent user turn before the last
assistant turn already contains the sentinel "[no-emdash-hook]", the
hook exits 0 unconditionally — Claude already had one correction pass
and we don't chain another injection regardless of the result.
"""
from __future__ import annotations

import json
import os
import sys

from cccs_hooks.transcript import TranscriptNotFound, Turn, load_transcript

_SENTINEL = "[no-emdash-hook]"
_EM_DASH = "—"

_CORRECTION_MSG = (
    f"{_SENTINEL} Your previous response contained one or more em-dashes (—). "
    "Replace each em-dash with a space-surrounded hyphen ( - ) and restate only "
    "the affected sentence(s). Do not repeat sentences that had no em-dashes."
)


def _last_assistant_text(turns: list[Turn]) -> str | None:
    """Return the text of the most recent assistant turn, or None if absent."""
    for turn in reversed(turns):
        if turn.role == "assistant":
            return turn.content
    return None


def _last_user_before_last_assistant(turns: list[Turn]) -> str | None:
    """Return the text of the most recent user turn before the last assistant turn."""
    last_asst_idx: int | None = None
    for idx in range(len(turns) - 1, -1, -1):
        if turns[idx].role == "assistant":
            last_asst_idx = idx
            break
    if last_asst_idx is None:
        return None
    for idx in range(last_asst_idx - 1, -1, -1):
        if turns[idx].role == "user":
            return turns[idx].content
    return None


def check(turns: list[Turn]) -> int:
    """Return 2 (injecting correction) if em-dash found and not in correction pass; else 0.

    Side effects when returning 2:
    - Prints the correction instruction to stdout (injected by Claude Code as context).
    - Prints a status note to stderr (visible to the user).
    """
    prev_user = _last_user_before_last_assistant(turns)
    if prev_user is not None and _SENTINEL in prev_user:
        return 0

    last_asst = _last_assistant_text(turns)
    if last_asst is None:
        return 0

    if _EM_DASH not in last_asst:
        return 0

    print(_CORRECTION_MSG)
    print(
        f"{_SENTINEL} Em-dash detected in assistant response; requesting correction.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        data: dict[str, object] = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(f"[no-emdash-hook] malformed hook input JSON: {exc}", file=sys.stderr)
        return 0
    if not isinstance(data, dict):
        return 0

    session_id = str(data.get("session_id", ""))
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or str(data.get("cwd", ""))

    if not session_id or not cwd:
        return 0

    try:
        turns = load_transcript(session_id=session_id, cwd=cwd)
    except TranscriptNotFound as exc:
        print(f"[no-emdash-hook] transcript not found: {exc}", file=sys.stderr)
        return 0

    return check(turns)


if __name__ == "__main__":
    sys.exit(main())
