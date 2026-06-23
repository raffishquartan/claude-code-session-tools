from __future__ import annotations

import pytest

from cc_session_tools.lib.scheduler.jobspec import (
    CoalesceKind,
    JobSpec,
    JobValidationError,
    validate_job_fields,
)


def _valid() -> JobSpec:
    return validate_job_fields(
        job_id="tesco-shop-check",
        cadence="daily@09:00",
        coalesce="one",
        command=["ccst", "hooks", "run", "check-tesco-due"],
        surface=True,
        enabled=True,
        catchup_window="7d",
        timeout="60s",
    )


def test_valid_record_builds() -> None:
    spec = _valid()
    assert spec.job_id == "tesco-shop-check"
    assert spec.coalesce is CoalesceKind.ONE
    assert spec.command == ("ccst", "hooks", "run", "check-tesco-due")


def test_jobspec_is_frozen() -> None:
    spec = _valid()
    with pytest.raises(AttributeError):
        spec.job_id = "x"  # type: ignore[misc]


@pytest.mark.parametrize("bad_id", ["", "Tesco", "-lead", "trail-", "has space", "под"])
def test_bad_id_rejected(bad_id: str) -> None:
    with pytest.raises(JobValidationError):
        validate_job_fields(
            job_id=bad_id, cadence="daily@09:00", coalesce="one",
            command=["x"], surface=True, enabled=True,
            catchup_window="7d", timeout="60s",
        )


@pytest.mark.parametrize(
    "bad_cadence",
    ["hourly", "every:2w@from=not-a-date", "monthly:thu#9@09:00", "monthly:thu#0@09:00"],
)
def test_bad_cadence_rejected(bad_cadence: str) -> None:
    with pytest.raises(JobValidationError):
        validate_job_fields(
            job_id="j", cadence=bad_cadence, coalesce="one", command=["x"],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
        )


def test_anchored_and_nth_cadences_accepted() -> None:
    for good in ("every:2w@from=2026-01-09", "monthly:thu#3@09:00", "monthly:fri#last@18:00"):
        spec = validate_job_fields(
            job_id="j", cadence=good, coalesce="one", command=["x"],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
        )
        assert spec.cadence == good


def test_empty_command_rejected() -> None:
    with pytest.raises(JobValidationError):
        validate_job_fields(
            job_id="j", cadence="daily@09:00", coalesce="one", command=[],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
        )


def test_command_with_empty_arg_rejected() -> None:
    with pytest.raises(JobValidationError):
        validate_job_fields(
            job_id="j", cadence="daily@09:00", coalesce="one", command=["ok", ""],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
        )


def test_bad_coalesce_rejected() -> None:
    with pytest.raises(JobValidationError):
        validate_job_fields(
            job_id="j", cadence="daily@09:00", coalesce="sometimes", command=["x"],
            surface=True, enabled=True, catchup_window="7d", timeout="60s",
        )


@pytest.mark.parametrize("field,bad", [("catchup_window", "0d"), ("timeout", "-5s")])
def test_bad_durations_rejected(field: str, bad: str) -> None:
    kwargs = dict(
        job_id="j", cadence="daily@09:00", coalesce="one", command=["x"],
        surface=True, enabled=True, catchup_window="7d", timeout="60s",
    )
    kwargs[field] = bad
    with pytest.raises(JobValidationError):
        validate_job_fields(**kwargs)  # type: ignore[arg-type]
