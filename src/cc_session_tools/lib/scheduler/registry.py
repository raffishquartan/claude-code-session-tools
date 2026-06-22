"""jobs.toml registry I/O. Reads with stdlib tomllib; writes with a small,
schema-specific serialiser (the registry shape is fully controlled here). Each
record is validated through jobspec.validate_job_fields on load; duplicate ids
and malformed TOML raise RegistryError."""
from __future__ import annotations

import tomllib
from pathlib import Path

from cc_session_tools.lib.scheduler.jobspec import (
    JobSpec,
    JobValidationError,
    validate_job_fields,
)
from cc_session_tools.lib.scheduler.state import scheduler_dir

_GENERATED_HEADER = (
    "# cc-scheduler job registry. Hand-editable; also written by `ccsched`.\n"
    "# Serialised by cc_session_tools.lib.scheduler.registry.\n"
)
_DEFAULTS = {
    "coalesce": "one",
    "surface": True,
    "enabled": True,
    "catchup_window": "7d",
    "timeout": "60s",
}


class RegistryError(ValueError):
    """Raised for unparseable jobs.toml, duplicate ids, or unknown-id mutations."""


def registry_path() -> Path:
    return scheduler_dir() / "jobs.toml"


def _spec_from_table(table: dict[str, object]) -> JobSpec:
    try:
        return validate_job_fields(
            job_id=str(table["id"]),
            cadence=str(table["cadence"]),
            coalesce=str(table.get("coalesce", _DEFAULTS["coalesce"])),
            command=[str(x) for x in table["command"]],  # type: ignore[union-attr]
            surface=bool(table.get("surface", _DEFAULTS["surface"])),
            enabled=bool(table.get("enabled", _DEFAULTS["enabled"])),
            catchup_window=str(table.get("catchup_window", _DEFAULTS["catchup_window"])),
            timeout=str(table.get("timeout", _DEFAULTS["timeout"])),
        )
    except KeyError as exc:
        raise RegistryError(f"job table missing required field: {exc}") from exc
    except JobValidationError as exc:
        raise RegistryError(f"invalid job in jobs.toml: {exc}") from exc


def load_registry() -> list[JobSpec]:
    path = registry_path()
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise RegistryError(f"jobs.toml is not valid TOML: {exc}") from exc
    specs: list[JobSpec] = []
    seen: set[str] = set()
    for table in data.get("job", []):
        spec = _spec_from_table(table)
        if spec.job_id in seen:
            raise RegistryError(f"duplicate job id in jobs.toml: {spec.job_id!r}")
        seen.add(spec.job_id)
        specs.append(spec)
    return specs


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _serialise(specs: list[JobSpec]) -> str:
    blocks: list[str] = [_GENERATED_HEADER]
    for s in specs:
        cmd = ", ".join(_toml_str(part) for part in s.command)
        blocks.append(
            "[[job]]\n"
            f"id = {_toml_str(s.job_id)}\n"
            f"cadence = {_toml_str(s.cadence)}\n"
            f"coalesce = {_toml_str(s.coalesce.value)}\n"
            f"command = [{cmd}]\n"
            f"surface = {str(s.surface).lower()}\n"
            f"enabled = {str(s.enabled).lower()}\n"
            f"catchup_window = {_toml_str(s.catchup_window)}\n"
            f"timeout = {_toml_str(s.timeout)}\n"
        )
    return "\n".join(blocks)


def _write(specs: list[JobSpec]) -> None:
    target = scheduler_dir()
    target.mkdir(parents=True, exist_ok=True)
    path = registry_path()
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(_serialise(specs))
    tmp.replace(path)


def add_job(spec: JobSpec) -> None:
    specs = load_registry()
    if any(s.job_id == spec.job_id for s in specs):
        raise RegistryError(f"job id already exists: {spec.job_id!r}")
    specs.append(spec)
    _write(specs)


def replace_job(spec: JobSpec) -> None:
    specs = load_registry()
    if not any(s.job_id == spec.job_id for s in specs):
        raise RegistryError(f"unknown job id: {spec.job_id!r}")
    _write([spec if s.job_id == spec.job_id else s for s in specs])


def remove_job(job_id: str) -> None:
    specs = load_registry()
    kept = [s for s in specs if s.job_id != job_id]
    if len(kept) == len(specs):
        raise RegistryError(f"unknown job id: {job_id!r}")
    _write(kept)


def set_enabled(job_id: str, enabled: bool) -> None:
    specs = load_registry()
    found = False
    new: list[JobSpec] = []
    for s in specs:
        if s.job_id == job_id:
            found = True
            new.append(
                JobSpec(
                    job_id=s.job_id, cadence=s.cadence, coalesce=s.coalesce,
                    command=s.command, surface=s.surface, enabled=enabled,
                    catchup_window=s.catchup_window, timeout=s.timeout,
                )
            )
        else:
            new.append(s)
    if not found:
        raise RegistryError(f"unknown job id: {job_id!r}")
    _write(new)
