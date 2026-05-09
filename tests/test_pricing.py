"""Tests for the pricing module."""

from __future__ import annotations

import pandas as pd

from claude_code_usage import pricing


def test_lookup_returns_rates_for_known_model() -> None:
    rates = pricing.lookup("claude-opus-4-7")
    assert rates["input"] > 0
    assert rates["output"] > rates["input"]
    assert rates["cache_read"] < rates["input"]


def test_lookup_falls_back_to_family_for_unknown_id() -> None:
    rates = pricing.lookup("claude-opus-99-future-variant")
    assert rates == pricing.lookup("claude-opus-4-7")


def test_cost_for_usage_combines_token_buckets() -> None:
    # 1M input + 1M output for opus-4-7 = $15 + $75 = $90
    cost = pricing.cost_for_usage(
        model="claude-opus-4-7",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_5m=0,
        cache_creation_1h=0,
        cache_read=0,
    )
    assert cost == 90.0


def test_cost_for_usage_includes_cache_buckets() -> None:
    # cache_read at $1.5/M, cache_create_1h at $30/M
    cost = pricing.cost_for_usage(
        model="claude-opus-4-7",
        input_tokens=0,
        output_tokens=0,
        cache_creation_5m=0,
        cache_creation_1h=1_000_000,
        cache_read=1_000_000,
    )
    assert cost == 31.5


def test_add_cost_column_vectorised() -> None:
    df = pd.DataFrame(
        [
            {
                "model": "claude-opus-4-7",
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "cache_creation_5m": 0,
                "cache_creation_1h": 0,
                "cache_read": 0,
            },
            {
                "model": "claude-sonnet-4-6",
                "input_tokens": 1_000_000,
                "output_tokens": 0,
                "cache_creation_5m": 0,
                "cache_creation_1h": 0,
                "cache_read": 0,
            },
        ]
    )
    out = pricing.add_cost_column(df)
    assert list(out["cost_usd"]) == [90.0, 3.0]
