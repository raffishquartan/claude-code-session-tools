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
    # RAN only: a successful-but-nonzero-exit run's captured stdout (e.g. a
    # drift monitor's "found something" report). Always surfaced with a
    # warning wording, never folded into SUMMARY - see surface.py.
    findings: str | None = None
    # RAN only: a successful, zero-exit run's captured stdout (e.g. a verify
    # command's "all OK" confirmation). Always surfaced with neutral wording,
    # distinct from `findings`' warning wording - see surface.py. Mutually
    # exclusive with `findings`: a given run is either a clean 0-exit or a
    # findings-bearing nonzero exit, never both.
    output: str | None = None


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
            f"{report.consecutive_failures} consecutive failures{age_suffix} — see "
            f"`ccsched status {report.job_id}` / run `ccsched enable {report.job_id}` after fixing"
        )
    if report.outcome is Outcome.FAILED:
        age_suffix = f", {report.age}" if report.age else ""
        return (
            f"✗ {report.job_id} failed "
            f"({_ordinal(report.consecutive_failures)} consecutive{age_suffix}) — see "
            f"`ccsched status {report.job_id}`"
        )
    if report.outcome is Outcome.RAN:
        # A completed run is always shown, regardless of `surface` - unlike
        # LAUNCHED below, "it ran" is never something a job can silence.
        overdue = f" ({report.overdue} overdue)" if report.overdue else ""
        # Every RAN variant below carries this same age suffix (when set) so a
        # result read back from an earlier run is never mistaken for something
        # that just happened - e.g. sitting next to a fresh LAUNCHED line for
        # the same job from this same sweep.
        age_suffix = f", {report.age}" if report.age else ""
        if report.findings:
            # Bypasses the surface gate deliberately, same as FAILED/SUSPENDED
            # above: findings are signal, not routine noise a job can silence.
            header = f"⚠ {report.job_id} ran with findings{overdue}{age_suffix}:"
            body = "\n".join(f"  {line}" for line in report.findings.splitlines())
            return f"{header}\n{body}"
        if report.output:
            # A clean (0-exit) run's captured stdout - neutral wording, kept
            # distinct from the findings/warning case above so a passing check
            # is never mistaken for one that found something. One line-group:
            # never falls through to the bare "✓ ran" case below.
            header = f"✓ ran {report.job_id}{overdue}{age_suffix}:"
            body = "\n".join(f"  {line}" for line in report.output.splitlines())
            return f"{header}\n{body}"
        if report.count > 1:
            # Several unremarkable runs of the same job coalesced within one
            # sweep (§ coalesce fix) - one line with a count, not N
            # near-identical bare ticks. Only bare (no output/findings) runs
            # are ever coalesced - see surface.py - so this never competes
            # with the findings/output branches above.
            recency = f" (most recently {report.age})" if report.age else ""
            return f"✓ ran {report.job_id} ×{report.count}{recency}"
        base = f"✓ ran {report.job_id}{overdue}{age_suffix}"
        if report.deferred:
            base += f"\n⏳ {report.job_id}: {report.deferred} backfills deferred"
        if report.expired:
            base += f"\n   ({report.expired} missed run(s) dropped as expired)"
        return base
    # Only Outcome.LAUNCHED can reach here - SUMMARY/SUSPENDED/FAILED/RAN are
    # all fully handled above and always return. A "started" notice is not
    # "it ran", so it is the one outcome that still respects `surface`.
    if not report.surface:
        return None
    if report.count > 1:
        # Several launches of the same job coalesced within one sweep (§
        # coalesce fix) - see the matching RAN branch above.
        recency = f" (most recently {report.age})" if report.age else ""
        return f"▶ launched {report.job_id} ×{report.count}{recency}"
    age_suffix = f", {report.age}" if report.age else ""
    return f"▶ launched {report.job_id} (running in background){age_suffix}"


def format_digest(reports: list[JobReport], *, parse_error: str | None = None) -> str:
    if parse_error is not None:
        return f"[cc-scheduler] job registry failed to load — no jobs ran: {parse_error}"
    lines = [line for line in (_line(r) for r in reports) if line is not None]
    if not lines:
        return ""
    return "\n".join(["[cc-scheduler] scheduled-task catch-up:", *lines])
