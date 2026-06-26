"""8-digit confirmation guard hook.

Reads a CC PreToolUse JSON payload from stdin and decides whether to allow,
warn, or block the tool call. The decision rests on three transcript-based
checks (skill-marker exceptions short-circuit them):

  1. The latest user turn in the parent transcript is *exactly* 8 digits.
  2. The assistant turn immediately preceding that user turn offered the
     same 8-digit code via "Respond with ... <code>".
  3. No gated tool has already fired *after* that user turn (replay defence).

If all three hold and the assistant-to-user reply gap is under 30 minutes,
the call is allowed. Otherwise the hook either prints a warning (default,
``CCCS_ENFORCE_8DIGIT=warn``) or blocks with exit 2 (``=block``).

Skill-marker exceptions live under ``~/.cache/claude/markers/`` (override with
the ``CCCS_MARKERS_DIR`` env var) and have a 1-hour TTL based on file mtime.
Each marker permits a narrowly-scoped tool + input combination per CLAUDE.md.

Fail-safe: when the transcript cannot be located the hook ALWAYS exits 2
with a clear message, regardless of CCCS_ENFORCE_8DIGIT, because we cannot
verify confirmation without it.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Literal

from cccs_hooks.transcript import (
    TranscriptNotFound,
    Turn,
    find_intervening_gated_call,
    find_preceding_assistant_offer,
    find_recent_8digit,
    load_transcript,
)

_MARKER_TTL_S = 60 * 60  # 1 hour
_GAP_LIMIT_S = 30 * 60  # 30 minutes between offer and reply

GATED_TOOLS_DEFAULT: list[str] = [
    "mcp__whatsapp__send_message",
    "mcp__google-workspace__send_gmail_message",
    "mcp__opentabs__plugin_mark_reviewed",
    "mcp__opentabs__gwr_confirm_booking",
    "mcp__opentabs__tesco_create_order",
    "mcp__opentabs__tesco_place_order",
]


@dataclasses.dataclass(frozen=True, slots=True)
class VerificationResult:
    exit_code: Literal[0, 2]
    message: str  # printed to stderr; empty on clean allow


# ---------- skill-marker exceptions ----------


def _markers_dir() -> Path:
    custom = os.environ.get("CCCS_MARKERS_DIR")
    if custom:
        return Path(custom)
    cache_home = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache_home) / "claude" / "markers"


def _marker_fresh(name: str) -> bool:
    p = _markers_dir() / name
    if not p.exists():
        return False
    try:
        age = time.time() - p.stat().st_mtime
    except OSError:
        return False
    return age <= _MARKER_TTL_S


def _check_marker_exception(
    tool_name: str, tool_input: dict[str, object]
) -> str | None:
    """Return a non-empty reason string if a marker exception applies and
    the tool/input combination matches its strict scope. Otherwise None."""

    if tool_name in ("mcp__opentabs__tesco_create_order", "mcp__opentabs__tesco_place_order"):
        if _marker_fresh("tesco_shop_active"):
            return "tesco_shop_active marker (do-tesco-shop skill)"

    if tool_name == "mcp__whatsapp__send_message" and _marker_fresh("telegram_notify"):
        recipient = str(tool_input.get("recipient", ""))
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if chat_id and recipient == chat_id:
            return "telegram_notify marker (notify-user skill)"

    if tool_name == "mcp__google-workspace__send_gmail_message":
        subject = str(tool_input.get("subject", ""))
        if _marker_fresh("calendar_sync_email") and subject.startswith(
            "[Cld] Calendar sync "
        ):
            return "calendar_sync_email marker"
        if _marker_fresh("sleep_nudges_email") and subject.startswith(
            "[Cld] Sleep Nudge"
        ):
            return "sleep_nudges_email marker"

    return None


# ---------- self-send exception ----------


def _check_self_send_exception(
    tool_name: str, tool_input: dict[str, object]
) -> str | None:
    """Return a reason string if this is a Gmail send addressed solely to the
    user's own address, from their own address, with no cc/bcc. Otherwise None.

    A message whose only recipient is the sender cannot exfiltrate anything, so
    it is exempt from the 8-digit gate. The self address comes from the
    ``NOTIFY_EMAIL`` environment variable - never hardcoded, because this module
    ships in a public repo. If ``NOTIFY_EMAIL`` is unset the exemption never
    fires and the call falls through to the normal transcript-based gate.
    """
    if tool_name != "mcp__google-workspace__send_gmail_message":
        return None
    self_email = os.environ.get("NOTIFY_EMAIL", "").strip().lower()
    if not self_email:
        return None
    to = str(tool_input.get("to", "")).strip().lower()
    # Effective sender: an explicit Send-As alias if given, else the
    # authenticated account. Either must equal the self address.
    sender = str(
        tool_input.get("from_email") or tool_input.get("user_google_email") or ""
    ).strip().lower()
    cc = str(tool_input.get("cc") or "").strip()
    bcc = str(tool_input.get("bcc") or "").strip()
    if to == self_email and sender == self_email and not cc and not bcc:
        return f"self-send to {self_email} (no cc/bcc)"
    return None


# ---------- gap check ----------


def _parse_iso(ts: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _gap_ok(turns: list[Turn], user_idx: int) -> bool:
    """True iff the most recent assistant turn before user_idx is within
    ``_GAP_LIMIT_S`` of the user turn. Missing/unparseable timestamps fail
    closed (return False)."""
    user_ts = _parse_iso(turns[user_idx].timestamp)
    if user_ts is None:
        return False
    for idx in range(user_idx - 1, -1, -1):
        if turns[idx].role == "assistant":
            asst_ts = _parse_iso(turns[idx].timestamp)
            if asst_ts is None:
                return False
            return (user_ts - asst_ts).total_seconds() <= _GAP_LIMIT_S
        if turns[idx].role == "user":
            return False
    return False


# ---------- main verify logic ----------


def verify(
    hook_input: dict[str, object], gated_tools: list[str]
) -> VerificationResult:
    tool_name = str(hook_input.get("tool_name", ""))
    tool_input_raw = hook_input.get("tool_input")
    tool_input = tool_input_raw if isinstance(tool_input_raw, dict) else {}
    session_id = str(hook_input.get("session_id", ""))
    # Prefer CLAUDE_PROJECT_DIR (always the project root) over hook_input["cwd"],
    # which for Bash tool calls reflects the bash working directory and changes
    # with `cd` — it is NOT reliable for locating the session transcript.
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or str(hook_input.get("cwd", ""))
    enforce = os.environ.get("CCCS_ENFORCE_8DIGIT", "warn").strip().lower()

    # 0. Only gated tools are subject to confirmation. Any other tool is
    #    allowed unconditionally - this makes a no-matcher catch-all hook
    #    registration safe (it would otherwise block every tool call).
    if tool_name not in gated_tools:
        return VerificationResult(exit_code=0, message="")

    # 1. Skill-marker exceptions short-circuit before any transcript lookup.
    marker_reason = _check_marker_exception(tool_name, tool_input)
    if marker_reason is not None:
        return VerificationResult(
            exit_code=0,
            message=f"[8digit-allow] {tool_name}: {marker_reason}",
        )

    # 1b. A self-send (to == from == NOTIFY_EMAIL, no cc/bcc) cannot reach
    #     anyone but the user, so it is exempt regardless of transcript state.
    self_send_reason = _check_self_send_exception(tool_name, tool_input)
    if self_send_reason is not None:
        return VerificationResult(
            exit_code=0,
            message=f"[8digit-allow] {tool_name}: {self_send_reason}",
        )

    # 2. Locate transcript. Missing transcript ALWAYS blocks - we cannot
    #    verify the confirmation without it.
    try:
        turns = load_transcript(session_id=session_id, cwd=cwd)
    except TranscriptNotFound as e:
        return VerificationResult(
            exit_code=2,
            message=(
                f"[8digit-block] cannot verify 8-digit confirmation for "
                f"{tool_name}: transcript not found ({e}). Re-run from a real "
                f"Claude Code session, or set the appropriate skill marker."
            ),
        )

    # 3. Three-part verification.
    failures: list[str] = []
    found = find_recent_8digit(turns)
    if found is None:
        failures.append("latest user turn is not exactly 8 digits")
        verified = False
        user_idx = -1
        code = ""
    else:
        code, user_idx = found
        if not find_preceding_assistant_offer(turns, user_idx, code):
            failures.append(
                f"no preceding assistant 'Respond with {code}' offer"
            )
        if find_intervening_gated_call(turns, user_idx, gated_tools):
            failures.append(
                "a gated tool has already fired after this user turn (replay)"
            )
        if not _gap_ok(turns, user_idx):
            failures.append(
                "assistant-to-user reply gap exceeds 30 minutes "
                "(or timestamps unparseable)"
            )
        verified = not failures

    if verified:
        return VerificationResult(
            exit_code=0,
            message=f"[8digit-allow] {tool_name}: code {code} verified",
        )

    detail = "; ".join(failures) if failures else "verification failed"
    if enforce == "block":
        return VerificationResult(
            exit_code=2,
            message=(
                f"[8digit-block] {tool_name}: {detail}. Generate a fresh "
                f"8-digit code, share it as 'Respond with NNNNNNNN ...', and "
                f"only proceed if the user replies with exactly that string."
            ),
        )
    return VerificationResult(
        exit_code=0,
        message=(
            f"[8digit-warn] {tool_name}: {detail}. "
            f"(Set CCCS_ENFORCE_8DIGIT=block to enforce.)"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(f"[8digit-warn] malformed hook input JSON: {e}", file=sys.stderr)
        return 0
    if not isinstance(data, dict):
        print("[8digit-warn] hook input is not a JSON object", file=sys.stderr)
        return 0
    result = verify(data, gated_tools=GATED_TOOLS_DEFAULT)
    if result.message:
        print(result.message, file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
