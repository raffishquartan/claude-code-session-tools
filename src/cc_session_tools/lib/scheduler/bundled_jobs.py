"""Single source of truth for ccsched jobs CCST provisions automatically at install time. Both
`ccst ccsched-jobs install` (cli/ccst.py) and the `ccst doctor` check (lib/doctor.py) import this
list so the installer and the health check can never disagree about what should be registered.
Add a new BundledJob here — do not invent a second place to list one."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BundledJob:
    job_id: str
    cadence: str
    coalesce: str
    catchup_window: str
    timeout: str
    surface: bool
    command: tuple[str, ...]


BUNDLED_CCSCHED_JOBS: tuple[BundledJob, ...] = (
    BundledJob(
        job_id="pm-session-output-reconcile",
        cadence="every:7d",
        coalesce="one",
        catchup_window="7d",
        timeout="300s",
        surface=True,
        command=("ccst", "pdata", "reconcile-session-output", "--all-projects"),
    ),
)
