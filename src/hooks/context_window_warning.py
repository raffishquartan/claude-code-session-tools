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
import re
import sys
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from hooks import model_info

THRESHOLD_ORANGE = 150_000
THRESHOLD_RED = 200_000


class TranscriptError(Exception):
    """Raised when the transcript is missing, unreadable, or has content but
    not a single parseable JSON line."""


SYNTHETIC_MODEL = "<synthetic>"
_TTL_MINUTES = {"5m": 5, "1h": 60}


@dataclass(frozen=True, slots=True)
class ContextUsage:
    """The context size and the prompt cache's state after the last API response.

    `cache_ttl` is "1h" or "5m": the lifetime of the most recent cache write in the
    transcript (a write of both kinds counts as 1h; a write with no breakdown as 5m, the
    API default), or "5m" if the session never wrote to the cache."""

    tokens: int
    model: str
    responded_at: datetime.datetime | None
    cache_ttl: str


def _write_lifetime(usage: dict) -> str | None:
    """The cache lifetime this response wrote to, or None if it wrote nothing."""
    if not int(usage.get("cache_creation_input_tokens", 0) or 0):
        return None
    breakdown = usage.get("cache_creation")
    if isinstance(breakdown, dict) and int(breakdown.get("ephemeral_1h_input_tokens", 0) or 0):
        return "1h"
    return "5m"


def _parse_timestamp(value: object) -> datetime.datetime:
    if not isinstance(value, str):
        raise TranscriptError("last assistant entry has no timestamp")
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise TranscriptError(f"unparseable timestamp {value!r}") from exc


def _current_context_tokens(transcript_path: Path) -> ContextUsage:
    """Context size, model id and cache state from the last non-sidechain assistant
    message's usage. Zero tokens and an empty model for a fresh session with no
    assistant usage yet - that is a legitimate state, not an error. Entries whose model is `<synthetic>` are
    skipped: Claude Code writes them (with all-zero usage) for things that are
    not API responses, such as a usage-limit notice, so they say nothing about
    the context size."""
    if not transcript_path.is_file():
        raise TranscriptError(f"transcript not found: {transcript_path}")

    saw_nonempty = False
    parsed = 0
    last_usage: dict | None = None
    last_model = ""
    last_timestamp: object = None
    cache_ttl = "5m"
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
                model = message.get("model") or ""
                if isinstance(usage, dict) and model != SYNTHETIC_MODEL:
                    last_usage = usage
                    last_model = model
                    last_timestamp = obj.get("timestamp")
                    cache_ttl = _write_lifetime(usage) or cache_ttl
    except OSError as exc:
        raise TranscriptError(f"cannot read transcript: {exc}") from exc

    if saw_nonempty and parsed == 0:
        raise TranscriptError("transcript present but no parseable JSON lines")

    if last_usage is None:
        return ContextUsage(0, "", None, cache_ttl)

    tokens = (
        int(last_usage.get("input_tokens", 0) or 0)
        + int(last_usage.get("cache_creation_input_tokens", 0) or 0)
        + int(last_usage.get("cache_read_input_tokens", 0) or 0)
    )
    return ContextUsage(tokens, last_model, _parse_timestamp(last_timestamp), cache_ttl)


def _k_tokens(tokens: int) -> int:
    return (tokens + 500) // 1000


def _pct_of_window(tokens: int, window: int) -> int:
    return (tokens * 100 + window // 2) // window


def _window_label(window: int) -> str:
    if window >= 1_000_000:
        return f"{window // 1_000_000}M"
    return f"{(window + 500) // 1000}k"


def _format_cost(*, tokens: int, price_per_mtok: float) -> str:
    """Cost of `tokens` at `price_per_mtok` (cache-read for a warm cache, cache-write
    for a cold one), truncated (not rounded) to 2dp - matches `bc`'s `scale=2`
    semantics from the bash original. Uses Decimal rather than native float
    arithmetic: a naive math.floor(raw * 100) / 100 on a float hits
    representation error for ordinary inputs (e.g. tokens=350_000,
    price=0.20 evaluates to 0.06999999999999999 in float, one cent short of
    the correct 0.07). Decimal(str(price)) avoids reintroducing the same
    float-representation problem by round-tripping the price through its
    float form first.
    """
    raw = Decimal(tokens) / Decimal(1_000_000) * Decimal(str(price_per_mtok))
    return str(raw.quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def _expiry_label(responded_at: datetime.datetime, cache_ttl: str) -> str:
    """Local HH:MM at which the cache lapses. An estimate that errs early: the warning's own
    continuation response refreshes the cache a few seconds after `responded_at`."""
    expiry = responded_at + datetime.timedelta(minutes=_TTL_MINUTES[cache_ttl])
    return expiry.astimezone().strftime("%H:%M")


def _is_overridden(session_id: str) -> bool:
    """True if /context-override has silenced warnings for this session.
    Function-local import: this is only reached past the 150k-token branch
    (the uncommon case for most Stop events), so keeping the sessions.db
    import off the common/silent path matches the lazy-import rationale
    used elsewhere in hooks for rarely-taken branches (e.g.
    pending_migration.py's _log_failure/_default_legacy_paths)."""
    from cc_session_tools.lib import context_overrides

    return context_overrides.get_override(session_id)


def _reason(
    *, tokens: int, window: int, price: float, cold_price: float, name: str, now: str, expiry: str, red: bool,
) -> str:
    k = _k_tokens(tokens)
    pct = _pct_of_window(tokens, window)
    label = _window_label(window)
    cost = _format_cost(tokens=tokens, price_per_mtok=price)
    cold = _format_cost(tokens=tokens, price_per_mtok=cold_price)
    emoji = "🔴" if red else "🟠"
    when = "when you get a natural break" if red else "whenever convenient"
    return (
        f"The context window has passed {k}k tokens - about {pct}% of {name}'s "
        f"{label}-token window. THIS IS A WARNING ONLY - output the two lines below at the "
        f"start of your response, then end your turn as you normally would. Do not restart "
        f"or repeat any work.\n"
        f"'{emoji} CONTEXT ({now}, cached till {expiry}): ~{k}k tokens - ~{pct}% of the "
        f"{label}-token window ({name}, ~${cost}/turn cache reads, ~${cold}/turn uncached). This is a nudge, not a limit - keep going; "
        f"/compact {when}.'\n"
        f"'(To silence this warning for the rest of the session, run /context-override.)'"
    )


REMEDIATION_HINT = (
    "To fix: add the id to src/hooks/model_info.py in claude-code-session-tools; if ccst is just out "
    "of date, run `uv tool upgrade cc-session-tools` (installed from PyPI with uv) or "
    "`uv tool install --reinstall <path to your checkout>` (local checkout)."
)

_ID_DISALLOWED = re.compile(r"[^A-Za-z0-9._:<>\[\]-]")


def _model_phrase(model_id: str) -> str:
    """How the unknown model is named in the message. Characters outside the set real ids use are
    replaced, so a corrupt or hostile transcript value cannot break the quoted lines the assistant
    is told to echo."""
    if not model_id:
        return "an unknown model (no model id was recorded)"
    return f"model `{_ID_DISALLOWED.sub('?', model_id)}`"


def _reason_unknown(
    *, tokens: int, window: int, price: float, cold_price: float, model_id: str, now: str, expiry: str,
    red: bool,
) -> str:
    """Message for a model id missing from model_info's table: the window and price are
    assumptions and the text says so, instead of presenting them as facts."""
    k = _k_tokens(tokens)
    pct = _pct_of_window(tokens, window)
    label = _window_label(window)
    cost = _format_cost(tokens=tokens, price_per_mtok=price)
    cold = _format_cost(tokens=tokens, price_per_mtok=cold_price)
    model = _model_phrase(model_id)
    emoji = "🔴" if red else "🟠"
    when = "when you get a natural break" if red else "whenever convenient"
    return (
        f"The context window has passed {k}k tokens. The session's {model} is not in ccst's model "
        f"table, so the window size is ASSUMED to be {label} tokens; the real window may be much "
        f"larger (1M for current models) and any percentage may be wrong. THIS IS A WARNING ONLY - "
        f"output the two lines below at the start of your response, then end your turn as you "
        f"normally would. Do not restart or repeat any work.\n"
        f"'{emoji} CONTEXT ({now}, cached till {expiry}): ~{k}k tokens used; window size ASSUMED "
        f"({label}) for {model} - the real window may be much larger, so ~{pct}% is unreliable. "
        f"~${cost}/turn cache reads assumes the default ${price:.2f}/MTok cache-read price and "
        f"~${cold}/turn uncached assumes the default ${cold_price:.2f}/MTok cache-write price. This is a nudge, not a limit - keep going; "
        f"/compact {when}. {REMEDIATION_HINT}'\n"
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
        usage = _current_context_tokens(Path(transcript_path_str))
    except TranscriptError as exc:
        print(f"context-window-warning: {exc}", file=sys.stderr)
        return 1

    tokens, model_id = usage.tokens, usage.model
    if tokens < THRESHOLD_ORANGE:
        return 0
    if _is_overridden(session_id):
        return 0

    window = model_info.context_window(model_id)
    price = model_info.cache_read_price_per_mtok(model_id)
    cold_price = model_info.cache_write_price_per_mtok(model_id, usage.cache_ttl)
    expiry = _expiry_label(usage.responded_at, usage.cache_ttl)
    now = datetime.datetime.now().strftime("%H:%M")
    red = tokens >= THRESHOLD_RED

    if model_info.is_known(model_id):
        reason = _reason(
            tokens=tokens, window=window, price=price, cold_price=cold_price,
            name=model_info.display_name(model_id), now=now, expiry=expiry, red=red,
        )
    else:
        reason = _reason_unknown(
            tokens=tokens, window=window, price=price, cold_price=cold_price, model_id=model_id,
            now=now, expiry=expiry, red=red,
        )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
