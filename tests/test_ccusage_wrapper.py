"""Tests for the ccusage subprocess wrapper."""

from __future__ import annotations

import json

from claude_code_usage import ccusage_wrapper as cw


def test_parse_daily_json_extracts_totals() -> None:
    payload = json.dumps(
        {
            "daily": [
                {
                    "date": "2026-04-15",
                    "inputTokens": 100,
                    "outputTokens": 200,
                    "cacheCreationTokens": 300,
                    "cacheReadTokens": 400,
                    "totalTokens": 1000,
                    "totalCost": 1.23,
                    "modelsUsed": ["claude-opus-4-7"],
                    "modelBreakdowns": [],
                }
            ],
            "totals": {
                "inputTokens": 100,
                "outputTokens": 200,
                "cacheCreationTokens": 300,
                "cacheReadTokens": 400,
                "totalCost": 1.23,
                "totalTokens": 1000,
            },
        }
    )
    result = cw.parse_daily_output(payload)
    assert result.totals.total_cost == 1.23
    assert result.totals.total_tokens == 1000
    assert len(result.daily) == 1
    assert result.daily[0].date == "2026-04-15"


def test_reconcile_matches_within_tolerance() -> None:
    ours = {"input_tokens": 1000, "output_tokens": 2000, "cache_read": 4000, "cache_creation": 3000}
    theirs = {"input_tokens": 1001, "output_tokens": 2002, "cache_read": 4004, "cache_creation": 3003}
    diff = cw.reconcile_totals(ours, theirs, tolerance=0.01)
    assert diff.passed
    assert diff.max_relative_diff < 0.01


def test_reconcile_flags_mismatch() -> None:
    ours = {"input_tokens": 1000, "output_tokens": 2000, "cache_read": 4000, "cache_creation": 3000}
    theirs = {"input_tokens": 2000, "output_tokens": 2000, "cache_read": 4000, "cache_creation": 3000}
    diff = cw.reconcile_totals(ours, theirs, tolerance=0.005)
    assert not diff.passed
    assert "input_tokens" in diff.failed_fields
