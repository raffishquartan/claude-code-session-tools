"""Per-job state store: registered_at / last_success / last_attempt /
consecutive_failures / in_flight, persisted to ``<scheduler-dir>/state.json``
via atomic .tmp-swap. The scheduler dir defaults to ~/.claude/cc-scheduler and
is env-overridable via CC_SCHEDULER_DIR (tests redirect through it)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEDULER_DIR_ENV = "CC_SCHEDULER_DIR"
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_UTC = timezone.utc


def scheduler_dir() -> Path:
    raw = os.environ.get(SCHEDULER_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude" / "cc-scheduler"


def state_path() -> Path:
    return scheduler_dir() / "state.json"


def format_ts(dt: datetime) -> str:
    return dt.astimezone(_UTC).strftime(_TS_FMT) if dt.tzinfo else dt.strftime(_TS_FMT)


def parse_ts_or_none(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.strptime(value, _TS_FMT).replace(tzinfo=_UTC)


@dataclass(frozen=True, slots=True)
class InFlight:
    pid: int
    started_at: str
    instants: int


@dataclass(frozen=True, slots=True)
class JobState:
    registered_at: str
    last_success: str | None
    last_attempt: str | None
    consecutive_failures: int
    in_flight: InFlight | None = None


def _in_flight_from(raw: object) -> InFlight | None:
    if not isinstance(raw, dict):
        return None
    return InFlight(
        pid=int(raw["pid"]),
        started_at=str(raw["started_at"]),
        instants=int(raw["instants"]),
    )


def load_all_state() -> dict[str, JobState]:
    path = state_path()
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    out: dict[str, JobState] = {}
    for job_id, fields in data.items():
        out[job_id] = JobState(
            registered_at=str(fields["registered_at"]),
            last_success=fields.get("last_success"),
            last_attempt=fields.get("last_attempt"),
            consecutive_failures=int(fields.get("consecutive_failures", 0)),
            in_flight=_in_flight_from(fields.get("in_flight")),
        )
    return out


def save_all_state(states: dict[str, JobState]) -> None:
    target = scheduler_dir()
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        job_id: {
            "registered_at": js.registered_at,
            "last_success": js.last_success,
            "last_attempt": js.last_attempt,
            "consecutive_failures": js.consecutive_failures,
            "in_flight": (
                None if js.in_flight is None
                else {
                    "pid": js.in_flight.pid,
                    "started_at": js.in_flight.started_at,
                    "instants": js.in_flight.instants,
                }
            ),
        }
        for job_id, js in states.items()
    }
    path = state_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def ensure_registered(
    states: dict[str, JobState], job_id: str, now: datetime
) -> JobState:
    """Return the job's state, stamping ``registered_at = now`` if absent so a
    hand-added job does not back-fill from epoch (§9.1). Mutates ``states``."""
    if job_id not in states:
        states[job_id] = JobState(
            registered_at=format_ts(now),
            last_success=None,
            last_attempt=None,
            consecutive_failures=0,
            in_flight=None,
        )
    return states[job_id]


def _replace(js: JobState, *, in_flight: InFlight | None) -> JobState:
    return JobState(
        registered_at=js.registered_at, last_success=js.last_success,
        last_attempt=js.last_attempt, consecutive_failures=js.consecutive_failures,
        in_flight=in_flight,
    )


def set_in_flight(job_id: str, *, pid: int, started_at: str, instants: int) -> None:
    """Atomic read-modify-write of one job's in_flight marker (§9.2)."""
    states = load_all_state()
    states[job_id] = _replace(states[job_id], in_flight=InFlight(pid, started_at, instants))
    save_all_state(states)


def clear_in_flight(job_id: str) -> None:
    """Atomic read-modify-write clearing one job's in_flight marker (§9.2)."""
    states = load_all_state()
    if job_id in states:
        states[job_id] = _replace(states[job_id], in_flight=None)
        save_all_state(states)
