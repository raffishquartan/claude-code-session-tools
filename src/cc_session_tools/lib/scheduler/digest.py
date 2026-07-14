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
    SUMMARY = "summary"


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
    # Relative-age suffix (e.g. "12d ago"). Always set by the surfacing layer
    # for FAILED/SUSPENDED reports, and for SUMMARY reports (oldest folded
    # entry); unused otherwise.
    age: str | None = None
    # SUMMARY only: number of routine entries folded into this one line.
    count: int = 0


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _line(report: JobReport) -> str | None:
    if report.outcome is Outcome.SUMMARY:
        return (
            f"⏱ {report.count} routine job runs since your last session, "
            f"oldest {report.age}"
        )
    if report.outcome is Outcome.SUSPENDED:
        age_suffix = f", {report.age}" if report.age else ""
        return (
            f"⛔ {report.job_id} auto-suspended after "
            f"{report.consecutive_failures} consecutive failures{age_suffix} — see fires.jsonl / "
            f"run `ccsched enable {report.job_id}` after fixing"
        )
    if report.outcome is Outcome.FAILED:
        age_suffix = f", {report.age}" if report.age else ""
        return (
            f"✗ {report.job_id} failed "
            f"({_ordinal(report.consecutive_failures)} consecutive{age_suffix}) — see fires.jsonl"
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
        return f"[cc-scheduler] job registry failed to load — no jobs ran: {parse_error}"
    lines = [line for line in (_line(r) for r in reports) if line is not None]
    if not lines:
        return ""
    return "\n".join(["[cc-scheduler] scheduled-task catch-up:", *lines])
