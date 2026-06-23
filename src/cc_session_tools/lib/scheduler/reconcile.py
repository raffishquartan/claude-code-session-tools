"""Reconcile + launch (§9.1): for each enabled job, decide what is owed since
its last success and LAUNCH a detached worker (`ccsched _run-job`) to run it —
never running the command here. There is no global lock; the per-job in-flight
lock acquired by the worker is the sole overlap guarantee, so a duplicate launch
is harmless. ``now`` and the ``spawn`` primitive are injected for testability."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from cc_session_tools.lib.scheduler import ledger, registry, state
from cc_session_tools.lib.scheduler.cadence import parse_cadence
from cc_session_tools.lib.scheduler.duration import parse_duration
from cc_session_tools.lib.scheduler.due import owed
from cc_session_tools.lib.scheduler.jobspec import CoalesceKind
from cc_session_tools.lib.scheduler.ledger import LedgerEntry, LedgerEvent
from cc_session_tools.lib.scheduler.lock import pid_alive
from cc_session_tools.lib.scheduler.runner import spawn_detached

__all__ = ["ReconcileResult", "Spawn", "reconcile_and_launch", "spawn_detached"]

logger = logging.getLogger(__name__)

_DEFAULT_LAUNCH_CAP = 20

Spawn = Callable[[list[str]], int]


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    launched: list[str]
    parse_error: str | None


def reconcile_and_launch(
    *,
    now: datetime,
    per_sweep_cap: int = _DEFAULT_LAUNCH_CAP,
    spawn: Spawn = spawn_detached,
) -> ReconcileResult:
    try:
        specs = registry.load_registry()
    except registry.RegistryError as exc:
        return ReconcileResult(launched=[], parse_error=str(exc))

    states = state.load_all_state()
    launched: list[str] = []
    for spec in specs:
        if not spec.enabled:
            continue
        js = state.ensure_registered(states, spec.job_id, now)
        if js.in_flight is not None and pid_alive(js.in_flight.pid):
            continue  # fast-path skip; not the correctness guarantee (§9.1)

        cadence = parse_cadence(spec.cadence)
        window = parse_duration(spec.catchup_window)
        baseline = state.parse_ts_or_none(js.last_success) or state.parse_ts_or_none(js.registered_at)
        assert baseline is not None  # guaranteed by ensure_registered
        result = owed(cadence, baseline, now, catchup_window=window)

        if result.expired_count:
            ledger.record(LedgerEntry(
                job_id=spec.job_id, event=LedgerEvent.SKIP_EXPIRED,
                owed=result.expired_count, ran=0, exit_code=None, duration_ms=0,
                error=None,
            ))
        if not result.instants:
            continue

        if len(launched) >= per_sweep_cap:
            ledger.record(LedgerEntry(
                job_id=spec.job_id, event=LedgerEvent.DEFER,
                owed=len(result.instants), ran=0, exit_code=None, duration_ms=0,
                error="launch cap reached",
            ))
            continue

        k = len(result.instants) if spec.coalesce is CoalesceKind.EACH else 1
        spawn(["ccsched", "_run-job", spec.job_id, "--instants", str(k)])
        ledger.record(LedgerEntry(
            job_id=spec.job_id, event=LedgerEvent.LAUNCH, owed=len(result.instants),
            ran=0, exit_code=None, duration_ms=0, error=None,
        ))
        launched.append(spec.job_id)

    # ensure_registered may have stamped registered_at for never-seen jobs.
    state.save_all_state(states)
    return ReconcileResult(launched=launched, parse_error=None)
