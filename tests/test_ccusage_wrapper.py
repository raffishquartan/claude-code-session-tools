"""Tests for the ccusage subprocess wrapper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


_DAILY_JSON = json.dumps(
    {
        "daily": [],
        "totals": {
            "inputTokens": 1,
            "outputTokens": 2,
            "cacheCreationTokens": 3,
            "cacheReadTokens": 4,
            "totalCost": 0.5,
            "totalTokens": 10,
        },
    }
)


def _install_fake_ccusage_in_bun_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bun_home = tmp_path / "bunhome"
    bin_dir = bun_home / "bin"
    bin_dir.mkdir(parents=True)
    exe = bin_dir / "ccusage"
    exe.write_text(f"#!/bin/sh\necho '{_DAILY_JSON}'\n")
    exe.chmod(0o755)
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("BUN_INSTALL", str(bun_home))


def test_run_daily_finds_ccusage_in_bun_bin_dir_without_path_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_ccusage_in_bun_bin(tmp_path, monkeypatch)

    assert cw.is_available()
    assert cw.run_daily().totals.total_cost == 0.5


def test_run_daily_error_points_at_setup_and_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("BUN_INSTALL", str(tmp_path / "no-bun"))

    with pytest.raises(cw.CcusageNotInstalled) as excinfo:
        cw.run_daily()

    assert "analyse-cc-usage" in str(excinfo.value)
    assert "ccst doctor" in str(excinfo.value)
