"""The typed job record and the single boundary validator. Once a JobSpec is
built, internals trust it; no re-validation downstream."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from cc_session_tools.lib.scheduler.cadence import CadenceError, parse_cadence
from cc_session_tools.lib.scheduler.duration import DurationError, parse_duration

_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class JobValidationError(ValueError):
    """Raised when a job record's fields fail boundary validation."""


class CoalesceKind(str, Enum):
    ONE = "one"
    EACH = "each"


@dataclass(frozen=True, slots=True)
class JobSpec:
    job_id: str
    cadence: str
    coalesce: CoalesceKind
    command: tuple[str, ...]
    surface: bool
    enabled: bool
    catchup_window: str
    timeout: str
    # Exit codes that count as "ran, did not crash" for suspend accounting.
    # A code in this set other than 0 is a job signalling findings (e.g. a
    # drift-monitor command whose exit 1 means "found something"), not a
    # failure - see worker.py's crashed/findings split.
    success_exit_codes: tuple[int, ...] = (0,)


def check_job_id(job_id: str) -> None:
    """Boundary validator for a bare job id, used both by validate_job_fields
    (a full spec) and by callers that only need to validate an id on its own
    - e.g. ccsched's `rename` command, which never builds a JobSpec."""
    if not _KEBAB_RE.match(job_id):
        raise JobValidationError(
            f"invalid job id {job_id!r}: must be lowercase kebab-case [a-z0-9-], "
            "no leading/trailing dash"
        )


def _check_command(command: list[str]) -> tuple[str, ...]:
    if len(command) < 1:
        raise JobValidationError("command must have at least one argv element")
    if any(not part for part in command):
        raise JobValidationError("command argv elements must all be non-empty")
    return tuple(command)


def _check_coalesce(coalesce: str) -> CoalesceKind:
    try:
        return CoalesceKind(coalesce)
    except ValueError as exc:
        raise JobValidationError(
            f"invalid coalesce {coalesce!r}: must be 'one' or 'each'"
        ) from exc


def _check_positive_duration(name: str, value: str) -> None:
    try:
        parse_duration(value)
    except DurationError as exc:
        raise JobValidationError(f"invalid {name} {value!r}: {exc}") from exc


def _check_success_exit_codes(codes: tuple[int, ...]) -> None:
    if not codes:
        raise JobValidationError("success_exit_codes must contain at least one code")
    for code in codes:
        if not (0 <= code <= 255):
            raise JobValidationError(f"invalid exit code {code!r}: must be 0-255")


def parse_success_exit_codes(raw: str) -> tuple[int, ...]:
    """Parse a CLI-facing comma-separated exit-code list, e.g. '0,1'.

    Boundary parser for ccsched's --success-exit-codes flag; raises
    JobValidationError on anything that isn't a valid int list.
    """
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise JobValidationError("success_exit_codes must contain at least one code")
    try:
        codes = tuple(int(p) for p in parts)
    except ValueError as exc:
        raise JobValidationError(f"invalid success_exit_codes {raw!r}: must be comma-separated integers") from exc
    _check_success_exit_codes(codes)
    return codes


def validate_job_fields(
    *,
    job_id: str,
    cadence: str,
    coalesce: str,
    command: list[str],
    surface: bool,
    enabled: bool,
    catchup_window: str,
    timeout: str,
    success_exit_codes: tuple[int, ...] = (0,),
) -> JobSpec:
    check_job_id(job_id)
    try:
        parse_cadence(cadence)
    except CadenceError as exc:
        raise JobValidationError(f"invalid cadence: {exc}") from exc
    coalesce_kind = _check_coalesce(coalesce)
    command_tuple = _check_command(command)
    _check_positive_duration("catchup_window", catchup_window)
    _check_positive_duration("timeout", timeout)
    _check_success_exit_codes(success_exit_codes)
    return JobSpec(
        job_id=job_id,
        cadence=cadence,
        coalesce=coalesce_kind,
        command=command_tuple,
        surface=surface,
        enabled=enabled,
        catchup_window=catchup_window,
        timeout=timeout,
        success_exit_codes=success_exit_codes,
    )
