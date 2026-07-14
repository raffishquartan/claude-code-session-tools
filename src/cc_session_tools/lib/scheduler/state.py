"""Per-job run state, backed by the `job_state` table in ccsched.db. Pure
helpers (timestamp formatting, next_failure_count) are unchanged; the storage
ops are now targeted single-row reads/writes instead of a whole-file state.json
read-modify-write — the fix for R2 (and the biggest efficiency win in this
phase: a single worker run went from 5 full loads + 4 full saves of every job's
state to a handful of single-row statements touching only its own job)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from cc_session_tools.lib.scheduler import store

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_UTC = timezone.utc
DEFAULT_SUSPEND_THRESHOLD = 10


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
    suspended: bool = False


def _row_to_state(row) -> JobState:
    in_flight = (
        None if row["in_flight_pid"] is None
        else InFlight(
            pid=int(row["in_flight_pid"]),
            started_at=row["in_flight_started_at"],
            instants=int(row["in_flight_instants"]),
        )
    )
    return JobState(
        registered_at=row["registered_at"],
        last_success=row["last_success"],
        last_attempt=row["last_attempt"],
        consecutive_failures=int(row["consecutive_failures"]),
        in_flight=in_flight,
        suspended=bool(row["suspended"]),
    )


def load_all_state() -> dict[str, JobState]:
    """Every job's state. Bulk read for `ccsched list` and reconcile's iteration;
    per-job mutators below never load the whole table."""
    conn = store.connect()
    try:
        rows = conn.execute("SELECT * FROM job_state").fetchall()
    finally:
        conn.close()
    return {r["job_id"]: _row_to_state(r) for r in rows}


def get_state(job_id: str) -> JobState | None:
    conn = store.connect()
    try:
        row = conn.execute("SELECT * FROM job_state WHERE job_id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_state(row) if row is not None else None


def save_all_state(states: dict[str, JobState]) -> None:
    """Per-row UPSERT of every supplied job's state in one transaction. Used for
    test seeding and the migration script (single-writer contexts). Production
    code paths use the targeted single-row ops below, never this."""
    conn = store.connect()
    try:
        for job_id, js in states.items():
            conn.execute(
                "INSERT INTO job_state (job_id, registered_at, last_success, "
                "last_attempt, consecutive_failures, suspended, in_flight_pid, "
                "in_flight_started_at, in_flight_instants) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET "
                "registered_at=excluded.registered_at, "
                "last_success=excluded.last_success, "
                "last_attempt=excluded.last_attempt, "
                "consecutive_failures=excluded.consecutive_failures, "
                "suspended=excluded.suspended, "
                "in_flight_pid=excluded.in_flight_pid, "
                "in_flight_started_at=excluded.in_flight_started_at, "
                "in_flight_instants=excluded.in_flight_instants",
                (
                    job_id, js.registered_at, js.last_success, js.last_attempt,
                    js.consecutive_failures, int(js.suspended),
                    None if js.in_flight is None else js.in_flight.pid,
                    None if js.in_flight is None else js.in_flight.started_at,
                    None if js.in_flight is None else js.in_flight.instants,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def ensure_registered_db(job_id: str, now: datetime) -> JobState:
    """Stamp registered_at=now for a never-seen job, then return its current
    state. INSERT OR IGNORE is a single write; a hand-added job (registry only)
    thus gets a state row on first sight without back-filling from epoch (§9.1).
    Replaces the old load-mutate-save-everything dance."""
    conn = store.connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO job_state (job_id, registered_at, last_success, "
            "last_attempt, consecutive_failures, suspended) VALUES (?,?,NULL,NULL,0,0)",
            (job_id, format_ts(now)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM job_state WHERE job_id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_state(row)


def set_in_flight(job_id: str, *, pid: int, started_at: str, instants: int) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "UPDATE job_state SET in_flight_pid=?, in_flight_started_at=?, "
            "in_flight_instants=? WHERE job_id=?",
            (pid, started_at, instants, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def clear_in_flight(job_id: str) -> None:
    conn = store.connect()
    try:
        conn.execute(
            "UPDATE job_state SET in_flight_pid=NULL, in_flight_started_at=NULL, "
            "in_flight_instants=NULL WHERE job_id=?",
            (job_id,),
        )
        conn.commit()
    finally:
        conn.close()


def clear_suspended(job_id: str) -> None:
    """Clear one job's suspended flag. No-op if the job has no state yet."""
    conn = store.connect()
    try:
        conn.execute("UPDATE job_state SET suspended=0 WHERE job_id=?", (job_id,))
        conn.commit()
    finally:
        conn.close()


def record_success(job_id: str, *, new_success: str, attempt_ts: str) -> None:
    """Advance last_success/last_attempt and reset the failure streak, preserving
    suspended and in_flight. Single-statement write."""
    conn = store.connect()
    try:
        conn.execute(
            "UPDATE job_state SET last_success=?, last_attempt=?, "
            "consecutive_failures=0 WHERE job_id=?",
            (new_success, attempt_ts, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def next_failure_count(
    consecutive_failures: int, *, suspended: bool, threshold: int = DEFAULT_SUSPEND_THRESHOLD
) -> tuple[int, bool, bool]:
    """Pure: (new_consecutive_failures, new_suspended, newly_suspended).
    newly_suspended is True only the instant the threshold is first crossed."""
    new_consecutive = consecutive_failures + 1
    newly_suspended = not suspended and new_consecutive >= threshold
    return new_consecutive, suspended or newly_suspended, newly_suspended
