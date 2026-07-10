"""Pure formatting of the SessionStart catch-up digest (§11). Takes structured
sweep results, returns a string. No I/O."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Outcome(str, Enum):
    RAN = "ran"
    FAILED = "failed"
    LAUNCHED = "launched"
    SUSPENDED = "suspended"


@dataclass(frozen=True, slots=True)
class JobReport:
    job_id: str
    outcome: Outcome
    surface: bool
    overdue: str
    ran: int
    deferred: int
    expired: int
    consecutive_failures: int


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _line(report: JobReport) -> str | None:
    if report.outcome is Outcome.SUSPENDED:
        return (
            f"⛔ {report.job_id} auto-suspended after "
            f"{report.consecutive_failures} consecutive failures — see fires.jsonl / "
            f"run `ccsched enable {report.job_id}` after fixing"
        )
    if report.outcome is Outcome.FAILED:
        return (
            f"✗ {report.job_id} failed "
            f"({_ordinal(report.consecutive_failures)} consecutive) — see fires.jsonl"
        )
    if not report.surface:
        return None
    if report.outcome is Outcome.LAUNCHED:
        return f"▶ launched {report.job_id} (running in background)"
    overdue = f" ({report.overdue} overdue)" if report.overdue else ""
    base = f"✓ ran {report.job_id}{overdue}"
    if report.deferred:
        base += f"\n⏳ {report.job_id}: {report.deferred} backfills deferred"
    if report.expired:
        base += f"\n   ({report.expired} missed run(s) dropped as expired)"
    return base


def format_digest(reports: list[JobReport], *, parse_error: str | None = None) -> str:
    if parse_error is not None:
        return f"[cc-scheduler] jobs.toml failed to parse — no jobs ran: {parse_error}"
    lines = [line for line in (_line(r) for r in reports) if line is not None]
    if not lines:
        return ""
    return "\n".join(["[cc-scheduler] scheduled-task catch-up:", *lines])
