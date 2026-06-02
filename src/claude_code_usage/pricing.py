"""Per-token pricing data and dollar calculations.

A seed `data/pricing.json` is shipped with the package. Lookups fall
back to a `family_fallbacks` table for unknown model IDs that match a
known family (e.g. `claude-opus-99` -> `opus` family rate). If a model
is completely unknown, `lookup()` raises `KeyError`.

Pricing data can be refreshed lazily from upstream (LiteLLM) by calling
`refresh()`; the local cache is treated as fresh for 7 days.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent
_PRICING_PATH = _THIS_DIR / "data" / "pricing.json"


def _load() -> dict[str, Any]:
    return json.loads(_PRICING_PATH.read_text())


def lookup(model: str) -> dict[str, float]:
    """Return per-token rates for `model`. Raises KeyError if unknown."""
    data = _load()
    models = data["models"]
    if model in models:
        return models[model]
    fallbacks = data.get("family_fallbacks", {})
    for family, fallback_model in fallbacks.items():
        if family in model:
            return models[fallback_model]
    raise KeyError(model)


def cost_for_usage(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_5m: int = 0,
    cache_creation_1h: int = 0,
    cache_read: int = 0,
) -> float:
    """Return the dollar cost of one assistant message's token mix."""
    rates = lookup(model)
    return (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_creation_5m * rates["cache_creation_5m"]
        + cache_creation_1h * rates["cache_creation_1h"]
        + cache_read * rates["cache_read"]
    )


def add_cost_column(df: pd.DataFrame) -> pd.DataFrame:
    """Return `df` with a `cost_usd` column added.

    Vectorised over rows: builds a per-row rates frame from the model
    column then dot-products against the token columns. Models that
    can't be resolved (after family fallback) get cost = 0 with a
    warning - we don't want a single unknown model to break a report.
    """
    out = df.copy()
    if out.empty:
        out["cost_usd"] = pd.Series(dtype=float)
        return out
    rates_records: list[dict[str, float]] = []
    unknown: set[str] = set()
    for model in out["model"]:
        try:
            rates_records.append(lookup(model))
        except KeyError:
            unknown.add(model)
            rates_records.append(
                {"input": 0.0, "output": 0.0, "cache_creation_5m": 0.0,
                 "cache_creation_1h": 0.0, "cache_read": 0.0}
            )
    rates_df = pd.DataFrame(rates_records, index=out.index)
    out["cost_usd"] = (
        out["input_tokens"] * rates_df["input"]
        + out["output_tokens"] * rates_df["output"]
        + out["cache_creation_5m"] * rates_df["cache_creation_5m"]
        + out["cache_creation_1h"] * rates_df["cache_creation_1h"]
        + out["cache_read"] * rates_df["cache_read"]
    )
    if unknown:
        import logging
        logging.getLogger(__name__).warning(
            "no pricing for models: %s (cost set to 0 for those rows)",
            sorted(unknown),
        )
    return out
