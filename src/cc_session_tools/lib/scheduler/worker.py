"""The detached worker (§9.2) behind `ccsched _run-job <id> --instants k`.

Acquires the per-job in-flight lock (sole overlap guarantee), stamps in_flight,
runs the command with a per-instant timeout, advances state on success, records
the outcome to the ledger, and ALWAYS clears in_flight + releases the lock. The
``now`` and ``runner`` are injected for testability."""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta

from cc_session_tools.lib.scheduler import ledger, registry, state
from cc_session_tools.lib.scheduler.cadence import parse_cadence
from cc_session_tools.lib.scheduler.duration import parse_duration
from cc_session_tools.lib.scheduler.due import owed
from cc_session_tools.lib.scheduler.jobspec import CoalesceKind, JobSpec
from cc_session_tools.lib.scheduler.ledger import LedgerEntry, LedgerEvent
from cc_session_tools.lib.scheduler.lock import InFlightLockHeld, in_flight_lock
from cc_session_tools.lib.scheduler.runner import RunOutcome, run_command
from cc_session_tools.lib.scheduler.state import JobState

logger = logging.getLogger(__name__)

Runner = Callable[[tuple[str, ...], timedelta], RunOutcome]


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


def _run_body(spec: JobSpec, instants: int, now: datetime, runner: Runner) -> None:
    timeout = parse_duration(spec.timeout)
    cadence = parse_cadence(spec.cadence)
    window = parse_duration(spec.catchup_window)
    states = state.load_all_state()
    js = state.ensure_registered(states, spec.job_id, now)
    baseline = state.parse_ts_or_none(js.last_success) or state.parse_ts_or_none(js.registered_at)
    assert baseline is not None
    result = owed(cadence, baseline, now, catchup_window=window)
    owed_n = len(result.instants)

    runs = instants if spec.coalesce is CoalesceKind.EACH else 1
    last_outcome: RunOutcome | None = None
    succeeded = 0
    for _ in range(runs):
        last_outcome = runner(spec.command, timeout)
        if last_outcome.timed_out or last_outcome.exit_code != 0:
            break
        succeeded += 1

    failed = last_outcome is None or last_outcome.timed_out or last_outcome.exit_code != 0
    attempt_ts = state.format_ts(now)
    states = state.load_all_state()  # reload to layer onto the in_flight stamp
    cur = states[spec.job_id]

    if failed:
        # Discard any j-1 successful instants from this run — intentional and
        # safe because jobs are idempotent-by-contract (re-running them is harmless).
        new_consecutive = cur.consecutive_failures + 1
        states[spec.job_id] = JobState(
            registered_at=cur.registered_at, last_success=cur.last_success,
            last_attempt=attempt_ts, consecutive_failures=new_consecutive,
            in_flight=cur.in_flight,
        )
        state.save_all_state(states)
        _record(spec, LedgerEvent.FAIL, owed_n, 0, last_outcome,
                (last_outcome.stderr.strip()[:200] if last_outcome else None)
                or ("timed out" if last_outcome and last_outcome.timed_out else None),
                consecutive_failures=new_consecutive)
        return

    if spec.coalesce is CoalesceKind.ONE:
        new_success = state.format_ts(now)
    else:
        new_success = state.format_ts(result.instants[succeeded - 1])
    states[spec.job_id] = JobState(
        registered_at=cur.registered_at, last_success=new_success,
        last_attempt=attempt_ts, consecutive_failures=0, in_flight=cur.in_flight,
    )
    state.save_all_state(states)
    # RUN only for a single on-time instant; BACKFILL when >1 owed or coalesced.
    event = LedgerEvent.RUN if owed_n <= 1 and succeeded == 1 else LedgerEvent.BACKFILL
    _record(spec, event, owed_n, succeeded, last_outcome, None)


def run_job(
    job_id: str, *, instants: int, now: datetime, runner: Runner = run_command
) -> None:
    spec = _load_spec(job_id)
    try:
        with in_flight_lock(job_id):
            try:
                # Ensure the job is registered in state.json before stamping
                # in_flight; a job added via `ccsched add` writes only to
                # jobs.toml. set_in_flight would KeyError without this guard.
                _states = state.load_all_state()
                state.ensure_registered(_states, job_id, now)
                state.save_all_state(_states)
                state.set_in_flight(
                    job_id, pid=os.getpid(), started_at=state.format_ts(now), instants=instants
                )
                _run_body(spec, instants, now, runner)
            finally:
                state.clear_in_flight(job_id)
    except InFlightLockHeld:
        logger.info("worker for %s exited: lock held by a live holder", job_id)
        return
