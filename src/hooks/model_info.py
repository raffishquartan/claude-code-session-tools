"""Model id -> context window size, standard input price, display name.

Ported verbatim from claude-code-config-sync's hooks/lib/model-info.sh (the
source of truth this table is migrating from). Consumed by
context_window_warning to phrase its nudge in terms of "% of the model's
actual window" and to estimate a per-model cache-read cost.

Figures current as of 2026-09-29 (Claude 5 family; 5.5/5.1 ids added). Context windows: Fable 5 /
Mythos 5 / Opus 5 / Opus 4.6-4.8 / Sonnet 5 / Sonnet 5.5 / Opus 5.5 / Fable 5.1 / Sonnet 4.6 all 1,000,000 tokens
(the default, not a beta long-context mode); Haiku 4.5 stays at 200,000.
Prices are the standard (non-cached) input rate per million tokens.

Re-check this table whenever a new model ships. A model not matched by any
case falls through to the default row (200000 tokens / $5.00 per MTok / "an
unrecognized model") - deliberately the same window as every pre-Claude-5
model, so an unrecognized model degrades to conservative,
already-correct-for-most-history behaviour rather than going quiet or firing
too early.

Deliberately not `claude_code_usage.pricing`: that table has no context-window
field and carries precise litellm-sourced rates for cost *accounting*, not
this module's flat "cache-read is ~0.1x standard input" nudge approximation.
Reusing it would drift the approximation onto a different number - see the
design spec's section 2.
"""
from __future__ import annotations

import re

_WINDOW_1M = {
    "claude-fable-5", "claude-mythos-5",
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5", "claude-sonnet-4-6",
    "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1",
}
_PRICE = {
    "claude-fable-5": 10.00, "claude-mythos-5": 10.00,
    "claude-opus-5": 5.00, "claude-opus-4-8": 5.00, "claude-opus-4-7": 5.00, "claude-opus-4-6": 5.00,
    "claude-sonnet-5": 3.00, "claude-sonnet-4-6": 3.00,
    # Assumed equal to the family's existing price (no published figure for these ids); the
    # price only feeds the rough "~$/turn in cache reads" estimate.
    "claude-sonnet-5-5": 3.00, "claude-opus-5-5": 5.00, "claude-fable-5-1": 10.00,
    "claude-haiku-4-5": 1.00,
}
_NAME = {
    "claude-fable-5": "Fable 5", "claude-mythos-5": "Mythos 5",
    "claude-opus-5": "Opus 5", "claude-opus-4-8": "Opus 4.8",
    "claude-opus-4-7": "Opus 4.7", "claude-opus-4-6": "Opus 4.6",
    "claude-sonnet-5": "Sonnet 5", "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5",
    "claude-fable-5-1": "Fable 5.1", "claude-haiku-4-5": "Haiku 4.5",
}
_DEFAULT_WINDOW = 200_000
_DEFAULT_PRICE = 5.00
_DEFAULT_NAME = "an unrecognized model"


_DATED_SNAPSHOT = re.compile(r"(.+)-\d{8}")


def _canonical(model_id: str) -> str:
    """A dated snapshot id (`<id>-YYYYMMDD`) resolves to its undated row. Applied once, and
    only for an exactly-eight-digit suffix: any other suffix (or a snapshot of an unknown id)
    stays unrecognised. `[1m]`/`-latest` are not handled - transcripts' `message.model` never
    carries them."""
    match = _DATED_SNAPSHOT.fullmatch(model_id)
    return match.group(1) if match else model_id


def context_window(model_id: str) -> int:
    return 1_000_000 if _canonical(model_id) in _WINDOW_1M else _DEFAULT_WINDOW


def input_price_per_mtok(model_id: str) -> float:
    return _PRICE.get(_canonical(model_id), _DEFAULT_PRICE)


def display_name(model_id: str) -> str:
    return _NAME.get(_canonical(model_id), _DEFAULT_NAME)
