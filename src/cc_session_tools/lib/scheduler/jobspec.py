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


def _check_id(job_id: str) -> None:
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
) -> JobSpec:
    _check_id(job_id)
    try:
        parse_cadence(cadence)
    except CadenceError as exc:
        raise JobValidationError(f"invalid cadence: {exc}") from exc
    coalesce_kind = _check_coalesce(coalesce)
    command_tuple = _check_command(command)
    _check_positive_duration("catchup_window", catchup_window)
    _check_positive_duration("timeout", timeout)
    return JobSpec(
        job_id=job_id,
        cadence=cadence,
        coalesce=coalesce_kind,
        command=command_tuple,
        surface=surface,
        enabled=enabled,
        catchup_window=catchup_window,
        timeout=timeout,
    )
