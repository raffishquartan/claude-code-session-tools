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
    success_exit_codes: tuple[int, ...] = (0,)


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
    BundledJob(
        job_id="pdata-verify-all",
        cadence="daily@03:00",
        coalesce="one",
        catchup_window="7d",
        timeout="300s",
        surface=False,
        command=("ccst", "pdata", "verify", "--all-projects"),
        # `verify --all-projects` exits 2 for "zero project .db files found" (plan Decision 8,
        # 2026-07-30-ccst-pdata-verify-and-skills.md) — a deliberate, distinct-from-clean CLI
        # result for interactive callers, but for this daily unattended job it's the expected
        # state on any machine that hasn't adopted pdata yet, not a crash. Without this, 10
        # consecutive not-yet-adopted days auto-suspends the job before it ever gets a chance to
        # run once pdata is adopted. A real per-project issue still exits 1, which isn't listed
        # here, so it still counts as a failure.
        success_exit_codes=(0, 2),
    ),
)
