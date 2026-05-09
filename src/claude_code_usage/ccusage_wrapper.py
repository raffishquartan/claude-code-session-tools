"""Subprocess wrapper around the upstream `ccusage` CLI.

`ccusage` (https://ccusage.com) is the authoritative source for dollar
totals by day / month / session because it tracks the upstream pricing
table. We use it for those figures and reconcile our token counts
against it. We do NOT use it for MCP/plugin/tool/project breakdowns -
that's our domain.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DailyEntry:
    date: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    total_tokens: int
    total_cost: float


@dataclass
class CcusageTotals:
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    total_tokens: int
    total_cost: float


@dataclass
class CcusageResult:
    totals: CcusageTotals
    daily: list[DailyEntry] = field(default_factory=list)


@dataclass
class ReconcileDiff:
    passed: bool
    max_relative_diff: float
    failed_fields: list[str] = field(default_factory=list)
    per_field: dict[str, float] = field(default_factory=dict)


class CcusageNotInstalled(RuntimeError):
    """Raised when ccusage is not on PATH."""


def is_available() -> bool:
    return shutil.which("ccusage") is not None


def run_daily(since: str | None = None, until: str | None = None,
              offline: bool = True) -> CcusageResult:
    """Invoke `ccusage daily --json` and parse the result.

    Dates are formatted as YYYYMMDD per ccusage's CLI.
    """
    if not is_available():
        raise CcusageNotInstalled("ccusage CLI is not on PATH; bun add -g ccusage")
    cmd = ["ccusage", "daily", "--json"]
    if offline:
        cmd.append("--offline")
    if since:
        cmd.extend(["--since", _to_yyyymmdd(since)])
    if until:
        cmd.extend(["--until", _to_yyyymmdd(until)])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return parse_daily_output(proc.stdout)


def _to_yyyymmdd(date: str) -> str:
    """Accept either YYYYMMDD or YYYY-MM-DD; emit YYYYMMDD."""
    return date.replace("-", "")


def parse_daily_output(payload: str) -> CcusageResult:
    data = json.loads(payload)
    totals = data.get("totals", {})
    daily = data.get("daily", [])
    return CcusageResult(
        totals=CcusageTotals(
            input_tokens=int(totals.get("inputTokens", 0)),
            output_tokens=int(totals.get("outputTokens", 0)),
            cache_creation_tokens=int(totals.get("cacheCreationTokens", 0)),
            cache_read_tokens=int(totals.get("cacheReadTokens", 0)),
            total_tokens=int(totals.get("totalTokens", 0)),
            total_cost=float(totals.get("totalCost", 0)),
        ),
        daily=[
            DailyEntry(
                date=d["date"],
                input_tokens=int(d.get("inputTokens", 0)),
                output_tokens=int(d.get("outputTokens", 0)),
                cache_creation_tokens=int(d.get("cacheCreationTokens", 0)),
                cache_read_tokens=int(d.get("cacheReadTokens", 0)),
                total_tokens=int(d.get("totalTokens", 0)),
                total_cost=float(d.get("totalCost", 0)),
            )
            for d in daily
        ],
    )


def reconcile_totals(
    ours: dict[str, float],
    theirs: dict[str, float],
    tolerance: float = 0.005,
) -> ReconcileDiff:
    """Return a diff record describing how our totals compare with ccusage's.

    `tolerance` is a relative tolerance applied per field. `passed` is
    True iff every field is within tolerance.
    """
    failed: list[str] = []
    per_field: dict[str, float] = {}
    for key in ours:
        their_val = theirs.get(key, 0)
        our_val = ours[key]
        denom = max(abs(their_val), abs(our_val), 1)
        rel = abs(our_val - their_val) / denom
        per_field[key] = rel
        if rel > tolerance:
            failed.append(key)
    return ReconcileDiff(
        passed=not failed,
        max_relative_diff=max(per_field.values()) if per_field else 0.0,
        failed_fields=failed,
        per_field=per_field,
    )
