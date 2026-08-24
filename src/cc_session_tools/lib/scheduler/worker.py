"""The detached worker (§9.2) behind `ccsched _run-job <id> --instants k`.

Acquires the per-job in-flight lock (sole overlap guarantee — unchanged from the
flat-file era, R3), stamps in_flight, runs the command with a per-instant
timeout, advances state on success via targeted single-row writes, records the
outcome to the ledger, and ALWAYS clears in_flight + releases the lock.

Also pushes every FAILED/RAN outcome via `notify.push_outcome` (§ visibility
fix) at the same points it records the ledger event — a SUSPEND still pushes
only via the existing `notify.suspended()` call, never doubled up with a
FAILED push for the same crash."""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
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
    result = owed(cadence, baseline, now, catchup_window=window)
    owed_n = len(result.instants)

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
        error_text = (
            (last_outcome.stderr.strip()[:200] if last_outcome else None)
            or ("timed out" if last_outcome and last_outcome.timed_out else None)
        )
        _record(spec, LedgerEvent.FAIL, owed_n, 0, last_outcome, error_text,
                consecutive_failures=new_consecutive)
        if newly_suspended:
            notify_suspended(spec.job_id, new_consecutive)
            _record(spec, LedgerEvent.SUSPEND, owed_n, 0, None, None,
                    consecutive_failures=new_consecutive)
        else:
            # SUSPENDED already pushed above via notify_suspended - a FAILED
            # push here too would double-notify for the same crash.
            notify_push(spec.job_id, Outcome.FAILED, error_text)
        return

    if spec.coalesce is CoalesceKind.ONE:
        new_success = state.format_ts(now)
    else:
        new_success = state.format_ts(result.instants[succeeded - 1])
    state.record_success(spec.job_id, new_success=new_success, attempt_ts=attempt_ts)
    event = LedgerEvent.RUN if owed_n <= 1 and succeeded == 1 else LedgerEvent.BACKFILL
    # Every successful run's stdout is captured into the ledger's error column
    # - not just a nonzero "found something" exit - so the digest/push always
    # has something to show beyond a bare checkmark when the command produced
    # output (see surface.py/digest.py, which render a clean vs a warning run
    # differently based on the ledger row's exit_code).
    captured_output = (
        last_outcome.stdout.strip()[:1000] or None if last_outcome is not None else None
    )
    _record(spec, event, owed_n, succeeded, last_outcome, captured_output)
    notify_push(spec.job_id, Outcome.RAN, captured_output)


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
