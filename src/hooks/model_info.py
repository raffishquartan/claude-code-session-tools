"""Model id -> context window size, cache-read price, display name.

Consumed by context_window_warning to phrase its nudge in terms of "% of the
model's actual window" and to estimate a per-turn cache-read cost.

Context windows as of 2026-09-29: Fable 5 / Mythos 5 / Opus 5 / Opus 4.6-4.8 /
Sonnet 5 / Sonnet 5.5 / Opus 5.5 / Fable 5.1 / Mythos 5.1 / Sonnet 4.6 all 1,000,000 tokens
(the default, not a beta long-context mode); Haiku 4.5 stays at 200,000.
Prices are each model's published first-party cache-read ("cache hits and
refreshes") rate per million tokens, from
https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-09-29).
They are not a fixed fraction of the input price: Fable 5.1 and Mythos 5.1 read at 0.025x
and Opus 5.5 at 0.05x, every other model here at 0.1x.

Re-check this table whenever a new model ships. A model not matched by any
case (`is_known` is False) falls through to the default window and price
(200000 tokens / $0.50 per MTok) - deliberately the same window as every
pre-Claude-5 model, so an unknown model degrades to conservative,
already-correct-for-most-history behaviour rather than going quiet or firing
too early. Those defaults are assumptions, and the warning says so; only
`display_name` has no default (it raises KeyError for an unknown id).

Deliberately not `claude_code_usage.pricing`: that module imports pandas, too
heavy for a hook that runs on every Stop event. tests/test_pricing.py asserts
that every cache-read price here matches that module's table.
"""
from __future__ import annotations

import re

_WINDOW_1M = {
    "claude-fable-5", "claude-mythos-5",
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5", "claude-sonnet-4-6",
    "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-mythos-5-1",
}
_CACHE_READ_PRICE = {
    "claude-fable-5-1": 0.25, "claude-mythos-5-1": 0.25, "claude-fable-5": 1.00, "claude-mythos-5": 1.00,
    "claude-opus-5-5": 0.20,
    "claude-opus-5": 0.50, "claude-opus-4-8": 0.50, "claude-opus-4-7": 0.50, "claude-opus-4-6": 0.50,
    "claude-sonnet-5-5": 0.20, "claude-sonnet-5": 0.20, "claude-sonnet-4-6": 0.30,
    "claude-haiku-4-5": 0.10,
}
_NAME = {
    "claude-fable-5": "Fable 5", "claude-mythos-5": "Mythos 5",
    "claude-opus-5": "Opus 5", "claude-opus-4-8": "Opus 4.8",
    "claude-opus-4-7": "Opus 4.7", "claude-opus-4-6": "Opus 4.6",
    "claude-sonnet-5": "Sonnet 5", "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5",
    "claude-fable-5-1": "Fable 5.1", "claude-mythos-5-1": "Mythos 5.1",
    "claude-haiku-4-5": "Haiku 4.5",
}
_DEFAULT_WINDOW = 200_000
_DEFAULT_CACHE_READ_PRICE = 0.50


_DATED_SNAPSHOT = re.compile(r"(.+)-\d{8}")


def _canonical(model_id: str) -> str:
    """A dated snapshot id (`<id>-YYYYMMDD`) resolves to its undated row. Applied once, and
    only for an exactly-eight-digit suffix: any other suffix (or a snapshot of an unknown id)
    stays unknown. `[1m]`/`-latest` are not handled - transcripts' `message.model` never
    carries them."""
    match = _DATED_SNAPSHOT.fullmatch(model_id)
    return match.group(1) if match else model_id


def context_window(model_id: str) -> int:
    return 1_000_000 if _canonical(model_id) in _WINDOW_1M else _DEFAULT_WINDOW


def cache_read_price_per_mtok(model_id: str) -> float:
    return _CACHE_READ_PRICE.get(_canonical(model_id), _DEFAULT_CACHE_READ_PRICE)


def is_known(model_id: str) -> bool:
    return _canonical(model_id) in _NAME


def display_name(model_id: str) -> str:
    """Raises KeyError for an unknown id: callers check `is_known` first."""
    return _NAME[_canonical(model_id)]
