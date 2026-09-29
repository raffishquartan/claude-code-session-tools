"""Tests for the pricing module."""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from claude_code_usage import pricing
from hooks import model_info

_MTOK = 1_000_000

# Published first-party rates per MTok: (input, output, 5m write, 1h write, cache read).
PUBLISHED = {
    "claude-fable-5-1": (10, 50, 12.50, 20, 0.25),
    "claude-mythos-5-1": (10, 50, 12.50, 20, 0.25),
    "claude-fable-5": (10, 50, 12.50, 20, 1),
    "claude-mythos-5": (10, 50, 12.50, 20, 1),
    "claude-opus-5-5": (4, 20, 5, 8, 0.20),
    "claude-opus-5": (5, 25, 6.25, 10, 0.50),
    "claude-opus-4-8": (5, 25, 6.25, 10, 0.50),
    "claude-opus-4-7": (5, 25, 6.25, 10, 0.50),
    "claude-opus-4-6": (5, 25, 6.25, 10, 0.50),
    "claude-opus-4-5": (5, 25, 6.25, 10, 0.50),
    "claude-opus-4-1": (15, 75, 18.75, 30, 1.50),
    "claude-opus-4": (15, 75, 18.75, 30, 1.50),
    "claude-sonnet-5-5": (2, 10, 2.50, 4, 0.20),
    "claude-sonnet-5": (2, 10, 2.50, 4, 0.20),
    "claude-sonnet-4-6": (3, 15, 3.75, 6, 0.30),
    "claude-sonnet-4-5": (3, 15, 3.75, 6, 0.30),
    "claude-sonnet-4": (3, 15, 3.75, 6, 0.30),
    "claude-haiku-4-5": (1, 5, 1.25, 2, 0.10),
}


def _cost_per_mtok_of_each_bucket(model: str) -> tuple[float, ...]:
    return tuple(
        round(pricing.cost_for_usage(model, **{"input_tokens": 0, "output_tokens": 0, bucket: _MTOK}), 6)
        for bucket in (
            "input_tokens", "output_tokens", "cache_creation_5m", "cache_creation_1h", "cache_read",
        )
    )


@pytest.mark.parametrize(("model", "rates"), PUBLISHED.items())
def test_listed_models_are_priced_at_published_rates(model: str, rates: tuple[float, ...]) -> None:
    assert _cost_per_mtok_of_each_bucket(model) == tuple(float(r) for r in rates)


def test_cost_for_usage_combines_token_buckets() -> None:
    # Opus 4.7: 1M input ($5) + 1M output ($25)
    cost = pricing.cost_for_usage(
        model="claude-opus-4-7",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_5m=0,
        cache_creation_1h=0,
        cache_read=0,
    )
    assert cost == pytest.approx(30.0)


def test_cost_for_usage_includes_cache_buckets() -> None:
    # Opus 4.7: cache_read $0.50/M + cache_create_1h $10/M
    cost = pricing.cost_for_usage(
        model="claude-opus-4-7",
        input_tokens=0,
        output_tokens=0,
        cache_creation_5m=0,
        cache_creation_1h=1_000_000,
        cache_read=1_000_000,
    )
    assert cost == pytest.approx(10.5)


def test_dated_snapshot_is_priced_as_its_undated_model() -> None:
    assert pricing.resolve("claude-opus-4-20250514") == ("claude-opus-4", False)
    assert pricing.lookup("claude-opus-4-1-20250805") == pricing.lookup("claude-opus-4-1")
    assert pricing.lookup("claude-haiku-4-5-20251001") == pricing.lookup("claude-haiku-4-5")


def test_exact_row_wins_over_dated_snapshot_stripping() -> None:
    assert pricing.resolve("claude-3-7-sonnet-20250219") == ("claude-3-7-sonnet-20250219", False)


@pytest.mark.parametrize(
    ("model", "row"),
    [
        ("claude-opus-9", "claude-opus-5-5"),
        ("claude-sonnet-9", "claude-sonnet-5-5"),
        ("claude-haiku-9", "claude-haiku-4-5"),
        ("claude-fable-9", "claude-fable-5-1"),
        ("claude-mythos-9", "claude-mythos-5-1"),
    ],
)
def test_unlisted_family_id_falls_back_to_newest_in_family(model: str, row: str) -> None:
    assert pricing.resolve(model) == (row, True)
    assert pricing.lookup(model) == pricing.lookup(row)


def test_dated_snapshot_of_an_unlisted_id_uses_the_family_fallback() -> None:
    assert pricing.resolve("claude-sonnet-5-9-20261001") == ("claude-sonnet-5-5", True)


@pytest.mark.parametrize(
    "model",
    [
        "gpt-oss-120b",
        # Unlisted Claude 3 ids are older than the table, so the newest-in-family price would be wrong.
        "claude-3-haiku-20240307",
        "claude-3-sonnet-20240229",
        # A family word outside a `claude-<family>-` id is not a Claude model.
        "magnum-opus-7b",
    ],
)
def test_model_without_row_or_family_id_raises_key_error(model: str) -> None:
    with pytest.raises(KeyError):
        pricing.lookup(model)


def test_synthetic_entries_cost_nothing() -> None:
    assert pricing.resolve("<synthetic>") == ("<synthetic>", False)
    assert set(pricing.lookup("<synthetic>").values()) == {0.0}


def _row(model: str, input_tokens: int = 1_000_000, output_tokens: int = 0) -> dict[str, object]:
    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_5m": 0,
        "cache_creation_1h": 0,
        "cache_read": 0,
    }


def test_add_cost_column_vectorised() -> None:
    df = pd.DataFrame(
        [
            _row("claude-opus-4-7", output_tokens=1_000_000),
            _row("claude-sonnet-4-6"),
        ]
    )
    out = pricing.add_cost_column(df)
    assert list(out["cost_usd"]) == pytest.approx([30.0, 3.0])


def test_add_cost_column_warns_about_unknown_models_and_zeroes_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    df = pd.DataFrame([_row("gpt-oss-120b"), _row("claude-sonnet-5-5")])
    with caplog.at_level(logging.WARNING, logger=pricing.__name__):
        out = pricing.add_cost_column(df)
    assert list(out["cost_usd"]) == pytest.approx([0.0, 2.0])
    assert "no pricing for models" in caplog.text
    assert "gpt-oss-120b" in caplog.text


def test_add_cost_column_names_models_priced_by_family_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    df = pd.DataFrame([_row("claude-opus-9"), _row("claude-opus-5-5")])
    with caplog.at_level(logging.WARNING, logger=pricing.__name__):
        out = pricing.add_cost_column(df)
    assert list(out["cost_usd"]) == pytest.approx([4.0, 4.0])
    assert "family fallback" in caplog.text
    assert "claude-opus-9" in caplog.text
    assert "claude-opus-5-5" in caplog.text


def test_add_cost_column_is_silent_for_synthetic_and_listed_models(
    caplog: pytest.LogCaptureFixture,
) -> None:
    df = pd.DataFrame([_row("<synthetic>", input_tokens=0), _row("claude-fable-5-1")])
    with caplog.at_level(logging.WARNING, logger=pricing.__name__):
        out = pricing.add_cost_column(df)
    assert list(out["cost_usd"]) == pytest.approx([0.0, 10.0])
    assert caplog.text == ""


@pytest.mark.parametrize("model", sorted(model_info._CACHE_READ_PRICE))
def test_context_warning_cache_read_prices_match_usage_pricing(model: str) -> None:
    """hooks.model_info keeps its own copy of cache-read prices (it must not import pandas);
    this keeps the two tables from drifting apart."""
    assert pricing.lookup(model)["cache_read"] * _MTOK == pytest.approx(
        model_info.cache_read_price_per_mtok(model)
    )
