"""Per-token pricing data and dollar calculations.

`data/pricing.json` is a hand-curated copy of Anthropic's published
first-party API rates (its `_source` / `_refreshed_at` fields say where
from and when); re-check it whenever a new model ships. Resolution order
for a model id:

1. an exact row;
2. a dated snapshot (`<id>-YYYYMMDD`, exactly eight digits, stripped
   once) resolves to the undated row;
3. an id containing `claude-<family>-` for a family in `family_fallbacks`
   is priced as that family's newest model - an estimate, which
   `add_cost_column()` reports in a warning. Claude 3 ids (`claude-3-...`)
   and non-Claude ids never match, so an unlisted old model is reported as
   unpriced rather than given a newer model's price;
4. otherwise `lookup()` raises `KeyError`.

`<synthetic>` (Claude Code's zero-usage stub entries) has an explicit
all-zero row.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pandas as pd

_THIS_DIR = Path(__file__).resolve().parent
_PRICING_PATH = _THIS_DIR / "data" / "pricing.json"
_DATED_SNAPSHOT = re.compile(r"(.+)-\d{8}")

logger = logging.getLogger(__name__)


def _load() -> dict[str, Any]:
    return json.loads(_PRICING_PATH.read_text())


def resolve(model: str) -> tuple[str, bool]:
    """Return (pricing row key, True if the row came from a family fallback).
    Raises KeyError if `model` has no row and no family match."""
    data = _load()
    models = data["models"]
    if model in models:
        return model, False
    dated = _DATED_SNAPSHOT.fullmatch(model)
    if dated and dated.group(1) in models:
        return dated.group(1), False
    for family, fallback_model in data["family_fallbacks"].items():
        if f"claude-{family}-" in model:
            return fallback_model, True
    raise KeyError(model)


def lookup(model: str) -> dict[str, float]:
    """Return per-token rates for `model`. Raises KeyError if unknown."""
    row, _ = resolve(model)
    rates: dict[str, float] = _load()["models"][row]
    return rates


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
    Models priced by family fallback are named in a separate warning, so
    an estimated price is never silent.
    """
    out = df.copy()
    if out.empty:
        out["cost_usd"] = pd.Series(dtype=float)
        return out
    models = _load()["models"]
    zero = {"input": 0.0, "output": 0.0, "cache_creation_5m": 0.0,
            "cache_creation_1h": 0.0, "cache_read": 0.0}
    rates_by_model: dict[str, dict[str, float]] = {}
    unknown: set[str] = set()
    fallback: dict[str, str] = {}
    for model in out["model"].unique():
        try:
            row, is_fallback = resolve(model)
        except KeyError:
            unknown.add(model)
            rates_by_model[model] = zero
            continue
        rates_by_model[model] = models[row]
        if is_fallback:
            fallback[model] = row
    rates_records = [rates_by_model[model] for model in out["model"]]
    rates_df = pd.DataFrame(rates_records, index=out.index)
    out["cost_usd"] = (
        out["input_tokens"] * rates_df["input"]
        + out["output_tokens"] * rates_df["output"]
        + out["cache_creation_5m"] * rates_df["cache_creation_5m"]
        + out["cache_creation_1h"] * rates_df["cache_creation_1h"]
        + out["cache_read"] * rates_df["cache_read"]
    )
    if unknown:
        logger.warning(
            "no pricing for models: %s (cost set to 0 for those rows)",
            sorted(unknown),
        )
    if fallback:
        logger.warning(
            "no exact pricing for models, priced by family fallback: %s",
            ", ".join(f"{model} as {row}" for model, row in sorted(fallback.items())),
        )
    return out
