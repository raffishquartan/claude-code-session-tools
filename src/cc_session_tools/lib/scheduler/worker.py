"""The detached worker (§9.2) behind `ccsched _run-job <id> --instants k`.

Acquires the per-job in-flight lock (sole overlap guarantee — unchanged from the
flat-file era, R3), stamps in_flight, runs the command with a per-instant
timeout, advances state on success via targeted single-row writes, records the
outcome to the ledger, and ALWAYS clears in_flight + releases the lock.

Also pushes every FAILED/RAN outcome via `notify.push_outcome` (§ visibility
fix) at the same points it records the ledger event — a SUSPEND still pushes
only via the existing `notify.suspended()` call, never doubled up with a
FAILED push for the same crash.

`classify_outcome`'s crash-path detail is captured from the TAIL of stderr,
not the head — see its docstring/`_crash_detail` for why."""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from cc_session_tools.lib.scheduler import ledger, notify, registry, state
from cc_session_tools.lib.scheduler.cadence import parse_cadence
from cc_session_tools.lib.scheduler.digest import Outcome
from cc_session_tools.lib.scheduler.duration import parse_duration
from cc_session_tools.lib.scheduler.due import owed
from cc_session_tools.lib.scheduler.jobspec import CoalesceKind, JobSpec
from cc_session_tools.lib.scheduler.ledger import LedgerEntry, LedgerEvent
from cc_session_tools.lib.scheduler.lock import InFlightLockHeld, in_flight_lock
from cc_session_tools.lib.scheduler.runner import RunOutcome, run_command
from cc_session_tools.lib.scheduler.state import DEFAULT_SUSPEND_THRESHOLD

logger = logging.getLogger(__name__)

Runner = Callable[[tuple[str, ...], timedelta], RunOutcome]
NotifySuspended = Callable[[str, int], bool]
NotifyPush = Callable[[str, Outcome, str | None], bool]


class UnknownJob(ValueError):
    """Raised when _run-job is given an id not in the registry."""


def _load_spec(job_id: str) -> JobSpec:
    for spec in registry.load_registry():
        if spec.job_id == job_id:
            return spec
    raise UnknownJob(f"unknown job id: {job_id!r}")


def _record(spec: JobSpec, event: LedgerEvent, owed_n: int, ran: int,
            outcome: RunOutcome | None, error: str | None,
            consecutive_failures: int = 0) -> None:
    ledger.record(LedgerEntry(
        job_id=spec.job_id, event=event, owed=owed_n, ran=ran,
        exit_code=(outcome.exit_code if outcome else None),
        duration_ms=(outcome.duration_ms if outcome else 0), error=error,
        consecutive_failures=consecutive_failures,
    ))


@dataclass(frozen=True, slots=True)
class OutcomeCapture:
    """Result of `classify_outcome` for one completed command run: whether it
    crashed against `spec.success_exit_codes`, the ledger event a single,
    uncoalesced attempt maps to (FAIL on crash, RUN on success — `_run_body`'s
    coalesce/backfill loop may override a RUN to BACKFILL itself afterwards;
    that decision needs owed/succeeded counts this function doesn't have, so
    it's out of scope here), and the truncated text recorded to the ledger
    and pushed — stderr's tail on crash (see `_crash_detail`), falling back to
    stdout's tail when stderr is empty (a controlled `sys.exit(1)` after a
    clean, expected-shape failure — e.g. `ccst pdata verify`'s "issues found"
    summary — prints its diagnostic to stdout, not stderr, and previously lost
    that text entirely on the crash path), then to "timed out" only when both
    streams are empty; stdout's head on success (1000 chars, captured
    unconditionally so a clean run's output still reaches the ledger/push,
    not just a nonzero "found something" exit)."""
    crashed: bool
    event: LedgerEvent
    detail: str | None


_CRASH_DETAIL_BUDGET = 500


def _crash_detail(stderr: str, *, budget: int = _CRASH_DETAIL_BUDGET) -> str | None:
    """The diagnostic text to surface for a crashed run: the TAIL of stderr,
    not the head. A Python traceback's actually-diagnostic line — the
    exception type and message (e.g. "RootsConfigError: CLAUDE_SESSION_TOOLS
    _REPO_ROOT is not set") — is its LAST line; the head is always the same
    "Traceback (most recent call last): File ..., line ..." boilerplate. The
    previous behaviour (`stderr[:200]`) captured only that boilerplate for
    any traceback longer than ~200 characters, so a job failing on an
    unhandled exception surfaced no diagnostic information at all in the
    ledger/digest/push — just a header naming no cause. A plain CLI tool
    whose last line of output is e.g. "ERROR: <reason>" gets the same
    benefit for the same reason.

    Applies uniformly to every job's crash path — no per-job or
    per-error-type special-casing — and stays a pure string operation with
    no dependency on how the failure happened.

    Cuts on a line boundary where one exists in the kept tail, so a
    multi-line message isn't sliced mid-word: takes the last `budget`
    characters, then (unless that already covers the whole string) drops
    everything up to and including the first newline in that slice, since
    that first newline marks a line truncated mid-way through by the
    windowing itself."""
    stripped = stderr.strip()
    if not stripped:
        return None
    if len(stripped) <= budget:
        return stripped
    tail = stripped[-budget:]
    newline = tail.find("\n")
    if 0 <= newline < len(tail) - 1:
        tail = tail[newline + 1:]
    return tail


def classify_outcome(
    spec: JobSpec, outcome: RunOutcome | None, *,
    notify_push: NotifyPush = notify.push_outcome, push: bool = True,
) -> OutcomeCapture:
    """Classify one completed run against `spec.success_exit_codes` and fire
    `notify_push` with the matching Outcome/detail, unless `push=False`. The
    single-attempt classification + capture + push shared by `_run_body`
    (which passes `push=False` when a SUSPENDED push already covers the same
    crash) and `ccsched.py`'s `_cmd_run` (which always pushes — a manual run
    has no suspend/coalesce concept to guard against). `outcome` is
    `RunOutcome | None` only to mirror `_run_body`'s zero-attempt edge case
    (a coalesce:each loop that ran zero times); `_cmd_run` always has a
    concrete outcome."""
    crashed = (
        outcome is None
        or outcome.timed_out
        or outcome.exit_code not in spec.success_exit_codes
    )
    if crashed:
        detail = _crash_detail(outcome.stderr) if outcome else None
        if detail is None and outcome is not None and not outcome.timed_out:
            detail = _crash_detail(outcome.stdout)
        if detail is None and outcome is not None and outcome.timed_out:
            detail = "timed out"
        if push:
            notify_push(spec.job_id, Outcome.FAILED, detail)
        return OutcomeCapture(crashed=True, event=LedgerEvent.FAIL, detail=detail)
    assert outcome is not None  # crashed is False only when an outcome exists
    detail = outcome.stdout.strip()[:1000] or None
    if push:
        notify_push(spec.job_id, Outcome.RAN, detail)
    return OutcomeCapture(crashed=False, event=LedgerEvent.RUN, detail=detail)


def _run_body(
    spec: JobSpec, instants: int, now: datetime, runner: Runner,
    notify_suspended: NotifySuspended, notify_push: NotifyPush,
) -> None:
    timeout = parse_duration(spec.timeout)
    cadence = parse_cadence(spec.cadence)
    window = parse_duration(spec.catchup_window)
    js = state.get_state(spec.job_id)
    assert js is not None  # ensure_registered_db ran in run_job before the lock body
    baseline = state.parse_ts_or_none(js.last_success) or state.parse_ts_or_none(js.registered_at)
    assert baseline is not None
    owed_result = owed(cadence, baseline, now, catchup_window=window)
    owed_n = len(owed_result.instants)

    runs = instants if spec.coalesce is CoalesceKind.EACH else 1
    last_outcome: RunOutcome | None = None
    succeeded = 0
    for _ in range(runs):
        last_outcome = runner(spec.command, timeout)
        if last_outcome.timed_out or last_outcome.exit_code not in spec.success_exit_codes:
            break
        succeeded += 1

    # "crashed" (drives suspend accounting) is distinct from "exited nonzero":
    # a job's success_exit_codes may include codes other than 0 to mean "ran
    # fine, found something" (e.g. a drift monitor) rather than "broke".
    crashed = (
        last_outcome is None
        or last_outcome.timed_out
        or last_outcome.exit_code not in spec.success_exit_codes
    )
    attempt_ts = state.format_ts(now)

    if crashed:
        new_consecutive, _new_suspended, newly_suspended = state.record_failure(
            spec.job_id, attempt_ts=attempt_ts, threshold=DEFAULT_SUSPEND_THRESHOLD,
        )
        # SUSPENDED already pushes via notify_suspended below - a FAILED push
        # here too would double-notify for the same crash.
        capture = classify_outcome(
            spec, last_outcome, notify_push=notify_push, push=not newly_suspended,
        )
        _record(spec, LedgerEvent.FAIL, owed_n, 0, last_outcome, capture.detail,
                consecutive_failures=new_consecutive)
        if newly_suspended:
            notify_suspended(spec.job_id, new_consecutive)
            _record(spec, LedgerEvent.SUSPEND, owed_n, 0, None, None,
                    consecutive_failures=new_consecutive)
        return

    if spec.coalesce is CoalesceKind.ONE:
        new_success = state.format_ts(now)
    else:
        new_success = state.format_ts(owed_result.instants[succeeded - 1])
    state.record_success(spec.job_id, new_success=new_success, attempt_ts=attempt_ts)
    event = LedgerEvent.RUN if owed_n <= 1 and succeeded == 1 else LedgerEvent.BACKFILL
    # Every successful run's stdout is captured into the ledger's error column
    # - not just a nonzero "found something" exit - so the digest/push always
    # has something to show beyond a bare checkmark when the command produced
    # output (see surface.py/digest.py, which render a clean vs a warning run
    # differently based on the ledger row's exit_code).
    capture = classify_outcome(spec, last_outcome, notify_push=notify_push)
    _record(spec, event, owed_n, succeeded, last_outcome, capture.detail)


def run_job(
    job_id: str, *, instants: int, now: datetime, runner: Runner = run_command,
    notify_suspended: NotifySuspended = notify.suspended,
    notify_push: NotifyPush = notify.push_outcome,
) -> None:
    spec = _load_spec(job_id)
    try:
        with in_flight_lock(job_id):
            try:
                # Register the state row before stamping in_flight; a job added
                # via `ccsched add` has a jobs row but no job_state row yet.
                state.ensure_registered_db(job_id, now)
                state.set_in_flight(
                    job_id, pid=os.getpid(), started_at=state.format_ts(now), instants=instants
                )
                _run_body(spec, instants, now, runner, notify_suspended, notify_push)
            finally:
                state.clear_in_flight(job_id)
    except InFlightLockHeld:
        logger.info("worker for %s exited: lock held by a live holder", job_id)
        return
