"""Stop hook: nudge to run /compact when the context window grows large.

  >= 150k tokens (orange): visible nudge, work continues.
  >= 200k tokens (red): more prominent nudge, work continues.

Ported from claude-code-config-sync's hooks/stop/context-window-warning.sh -
see the design spec (docs/superpowers/specs/2026-08-17-...-design.md) for the
full rationale. The two thresholds are fixed absolute token counts,
deliberately NOT scaled to the active model's context window: cache-read cost
and compaction cost scale with absolute tokens, not with what fraction of a
200k or a 1M window that represents. What DOES depend on the model is the
message text (via model_info), so the same absolute trigger doesn't read as
"nearly out of room" on a large-window model when it isn't. Neither tier uses
alarm language - both are non-blocking nudges, and the message says so.

Escape hatch: if the user has run /context-override this session (a per-
session row in sessions.db's context_overrides table, keyed by session_id),
the hook exits silently - no warning at all.

Fires at most once per turn (guarded by stop_hook_active, so the continuation
it triggers does not re-fire). A fresh session with no usage stays silent. A
genuine transcript-read error is surfaced to the user on stderr, non-blocking
(exit 1), so a parsing problem is visible but never wedges the session.
"""
from __future__ import annotations

import datetime
import json
import sys
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from cccs_hooks import model_info

THRESHOLD_ORANGE = 150_000
THRESHOLD_RED = 200_000


class TranscriptError(Exception):
    """Raised when the transcript is missing, unreadable, or has content but
    not a single parseable JSON line."""


def _current_context_tokens(transcript_path: Path) -> tuple[int, str]:
    """(token count, model id) from the last non-sidechain assistant message's
    usage. (0, "") for a fresh session with no assistant usage yet - that is a
    legitimate state, not an error."""
    if not transcript_path.is_file():
        raise TranscriptError(f"transcript not found: {transcript_path}")

    saw_nonempty = False
    parsed = 0
    last_usage: dict | None = None
    last_model = ""
    try:
        with transcript_path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                saw_nonempty = True
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                parsed += 1
                if obj.get("isSidechain") is True:
                    continue
                message = obj.get("message") or {}
                usage = message.get("usage")
                if isinstance(usage, dict):
                    last_usage = usage
                    last_model = message.get("model") or ""
    except OSError as exc:
        raise TranscriptError(f"cannot read transcript: {exc}") from exc

    if saw_nonempty and parsed == 0:
        raise TranscriptError("transcript present but no parseable JSON lines")

    if last_usage is None:
        return 0, ""

    tokens = (
        int(last_usage.get("input_tokens", 0) or 0)
        + int(last_usage.get("cache_creation_input_tokens", 0) or 0)
        + int(last_usage.get("cache_read_input_tokens", 0) or 0)
    )
    return tokens, last_model


def _k_tokens(tokens: int) -> int:
    return (tokens + 500) // 1000


def _pct_of_window(tokens: int, window: int) -> int:
    return (tokens * 100 + window // 2) // window


def _window_label(window: int) -> str:
    if window >= 1_000_000:
        return f"{window // 1_000_000}M"
    return f"{(window + 500) // 1000}k"


def _format_cost(*, tokens: int, price_per_mtok: float) -> str:
    """Cache-read cost, ~0.1x the model's standard input price, truncated
    (not rounded) to 2dp - matches `bc`'s `scale=2` semantics from the bash
    original. Uses Decimal rather than native float arithmetic: a naive
    math.floor(raw * 100) / 100 on a float hits representation error for
    ordinary inputs (e.g. tokens=700_000, price=3.00 evaluates to
    0.20999999999999996 in float, one cent short of the correct 0.21) -
    caught by adversarial code review on this exact function, not
    hypothetical. Decimal(str(price_per_mtok)) avoids reintroducing the same
    float-representation problem by round-tripping the price through its
    float form first.
    """
    raw = Decimal(tokens) / Decimal(1_000_000) * Decimal(str(price_per_mtok)) * Decimal("0.1")
    return str(raw.quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def _is_overridden(session_id: str) -> bool:
    """Stubbed until a later task lands lib.context_overrides; always False so
    every threshold/message test in this task is independent of the store."""
    return False


def _reason(*, tokens: int, window: int, price: float, name: str, now: str, red: bool) -> str:
    k = _k_tokens(tokens)
    pct = _pct_of_window(tokens, window)
    label = _window_label(window)
    cost = _format_cost(tokens=tokens, price_per_mtok=price)
    emoji = "🔴" if red else "🟠"
    when = "when you get a natural break" if red else "whenever convenient"
    return (
        f"The context window has passed {k}k tokens - about {pct}% of {name}'s "
        f"{label}-token window. THIS IS A WARNING ONLY - output the two lines below at the "
        f"start of your response, then end your turn as you normally would. Do not restart "
        f"or repeat any work.\n"
        f"'{emoji} CONTEXT ({now}): ~{k}k tokens - ~{pct}% of the {label}-token window "
        f"({name}, ~${cost}/turn in cache reads). This is a nudge, not a limit - keep going; "
        f"/compact {when}.'\n"
        f"'(To silence this warning for the rest of the session, run /context-override.)'"
    )


def main(argv: list[str] | None = None) -> int:
    try:
        stdin_obj = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        stdin_obj = {}

    if stdin_obj.get("stop_hook_active"):
        return 0

    transcript_path_str = stdin_obj.get("transcript_path") or ""
    if not transcript_path_str:
        return 0

    session_id = stdin_obj.get("session_id") or Path(transcript_path_str).stem

    try:
        tokens, model_id = _current_context_tokens(Path(transcript_path_str))
    except TranscriptError as exc:
        print(f"context-window-warning: {exc}", file=sys.stderr)
        return 1

    if tokens < THRESHOLD_ORANGE:
        return 0
    if _is_overridden(session_id):
        return 0

    window = model_info.context_window(model_id)
    price = model_info.input_price_per_mtok(model_id)
    name = model_info.display_name(model_id)
    now = datetime.datetime.now().strftime("%H:%M")
    red = tokens >= THRESHOLD_RED

    reason = _reason(tokens=tokens, window=window, price=price, name=name, now=now, red=red)
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
