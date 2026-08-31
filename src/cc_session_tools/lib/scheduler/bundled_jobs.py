"""Single source of truth for ccsched jobs CCST provisions automatically at install time. Both
`ccst ccsched-jobs install` (cli/ccst.py) and the `ccst doctor` check (lib/doctor.py) import this
list so the installer and the health check can never disagree about what should be registered.
Add a new BundledJob here — do not invent a second place to list one.

`diff_from_bundled` is the one comparison both callers use to decide whether an already-registered
job still matches its bundled definition, so "changed" is defined identically in both places. It
deliberately excludes `enabled`: that is per-machine operational state a human toggles via
`ccsched enable`/`disable`, not something a bundled definition ever expresses an opinion on — see
`_cmd_ccsched_jobs_install` and `doctor.check_ccsched_job_registered`, the two callers."""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cc_session_tools.lib.scheduler.jobspec import JobSpec

# Resolved at import time against *this* machine's home dir, never baked in as a literal -
# ccst skills install symlinks the bundled clean-hook-sessions skill to exactly this path, so
# the two can never point at different copies of the script.
_CLEAN_HOOK_SESSIONS_SCRIPT = str(
    Path.home() / ".claude" / "skills" / "clean-hook-sessions" / "scripts" / "clean-hook-sessions.py"
)


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
        # A completed run always surfaces its result regardless of this flag (surface.py:
        # only a LAUNCH notice - "started, running in background" - respects it); True means
        # you also see that launch notice, not just the eventual "ran: OK/issues" line.
        # Adopted from a live tweak - False was the original default, but the daily launch
        # notice for a fast job like this one turned out to be worth seeing after all.
        surface=True,
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
    BundledJob(
        job_id="pdata-sync-hourly",
        cadence="every:1h",
        coalesce="one",
        # Short on purpose, and NOT the "how far back may this backfill" knob it looks like.
        # coalesce="one" already pins the command to exactly one run per sweep (reconcile.py's
        # `k`, worker.py's `runs`), so no catchup_window can make a laptop that was asleep for a
        # week replay a week of cycles - one run reconciles all of it. All the window actually
        # changes is `owed_n`, which drives digest.py's "(N overdue)" annotation: "1d" would
        # render every post-weekend line as "(24 overdue)" and "7d" as "(168 overdue)", both
        # alarming and both meaningless. Instants outside the window become a SKIP_EXPIRED
        # ledger row that surface.py deliberately never renders, so a short window is quiet
        # rather than noisy. It cannot suppress the run either: an hourly cadence always has
        # exactly one instant inside the last hour, and 2h leaves an interval of headroom for
        # clock skew and for the gap between reconcile's `now` and the detached worker's.
        catchup_window="2h",
        # Matches pdata-verify-all rather than the 60s jobs: --all-projects can end in a
        # dump.write_latest(), a full DB serialize, for every project that has new local writes.
        timeout="300s",
        # False, unlike every sibling above: `surface` only gates the LAUNCH ("started, running
        # in background") notice - surface.py always shows a completed run regardless. Sweeps
        # are session-start driven, so this is by far the most frequent bundled job, and a
        # launch notice plus a completion line on every single session start is duplication.
        surface=False,
        command=("ccst", "pdata", "sync-check", "--all-projects"),
        # 1 = at least one project ended the cycle with an unresolved conflict. That is this job
        # doing its job (the ccst-doctor-drift-weekly precedent) - without it, ten consecutive
        # cycles with an unresolved fork would auto-suspend the very job that keeps reporting it.
        # 2 = a hard error, which includes the sibling-consistent "no project databases found":
        # on a machine that hasn't adopted pdata that is the expected state of every single run,
        # and this job runs hourly, so it would hit pdata-verify-all's documented auto-suspend
        # trap far faster than pdata-verify-all itself does. A timeout still counts as a crash
        # (worker.py checks timed_out before success_exit_codes), so the safety net is not gone.
        success_exit_codes=(0, 1, 2),
    ),
    BundledJob(
        job_id="ccst-doctor-drift-weekly",
        cadence="every:7d",
        coalesce="one",
        catchup_window="28d",
        timeout="60s",
        surface=True,
        command=("ccst", "doctor", "--drift"),
        # `--drift` exits 1 when it finds un-muted drift to report (see doctor.py's drift
        # monitor) — that is the job doing its job, not a crash, so it must not count against
        # auto-suspend or the weekly nudge would stop firing after ten quiet weeks.
        success_exit_codes=(0, 1),
    ),
    BundledJob(
        job_id="session-gc-report-weekly",
        cadence="every:7d",
        coalesce="one",
        catchup_window="28d",
        timeout="60s",
        surface=True,
        command=("ccst", "gc", "report"),
    ),
    BundledJob(
        job_id="update-command-cache-reminder",
        cadence="every:2w",
        coalesce="one",
        catchup_window="7d",
        timeout="30s",
        surface=True,
        command=(
            "echo",
            "Reminder: curate the bash-security-review command cache - run the "
            "update-command-cache skill to sweep fires.jsonl for new safe-verdict commands "
            "and promote the ones you approve.",
        ),
    ),
    BundledJob(
        job_id="telemetry-trim-weekly",
        cadence="every:7d",
        coalesce="one",
        catchup_window="28d",
        timeout="60s",
        surface=True,
        # --max-size 10 (MB) trimmed too aggressively in practice; 50 was adopted live and
        # is the better default.
        command=("ccst", "telemetry", "trim", "--max-size", "50", "--max-age-days", "90"),
    ),
    BundledJob(
        job_id="clean-hook-sessions-weekly",
        # Anchored (@from=<date>) rather than plain every:7d so the weekly run lands on a
        # fixed day, drift-free, matching the pattern already used for other anchored jobs
        # (e.g. a user's own every:2w@from=... jobs) - the specific date only fixes which
        # day of the week it recurs on, it does not restrict how far back it is owed.
        cadence="every:7d@from=2026-08-28",
        coalesce="one",
        catchup_window="112d",
        timeout="120s",
        surface=True,
        command=(
            "python3", _CLEAN_HOOK_SESSIONS_SCRIPT,
            "--older-than", "28", "--keep-n", "50", "--execute",
        ),
    ),
    BundledJob(
        job_id="ccsched-no-op-demoing-job-visibility",
        cadence="every:12h",
        coalesce="one",
        catchup_window="1d",
        timeout="10s",
        surface=True,
        command=(
            "echo",
            "ccsched notification check: this output reaching Telegram and the next "
            "SessionStart digest confirms the scheduled-job notification pipeline is working.",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class FieldDiff:
    """One field where a registered JobSpec no longer matches its bundled definition,
    carrying both the bundled and the current (live) value, rendered as display-ready
    strings so a caller never has to know a field's underlying type - see
    `diff_from_bundled_detail`."""
    field: str
    bundled: str
    current: str


def diff_from_bundled_detail(spec: "JobSpec", job: BundledJob) -> tuple[FieldDiff, ...]:
    """Same comparison as `diff_from_bundled`, but carries the before/after values for
    display, in declaration order; empty if the spec matches its bundled definition
    exactly. Deliberately ignores `enabled` — see module docstring. This is the one place
    that decides both *whether* a field changed and *how* its value is rendered, so
    `_cmd_ccsched_jobs_install` and `check_ccsched_job_registered` — the two callers —
    can never disagree on either question."""
    diffs: list[FieldDiff] = []
    if spec.cadence != job.cadence:
        diffs.append(FieldDiff("cadence", job.cadence, spec.cadence))
    if spec.coalesce.value != job.coalesce:
        diffs.append(FieldDiff("coalesce", job.coalesce, spec.coalesce.value))
    if spec.catchup_window != job.catchup_window:
        diffs.append(FieldDiff("catchup_window", job.catchup_window, spec.catchup_window))
    if spec.timeout != job.timeout:
        diffs.append(FieldDiff("timeout", job.timeout, spec.timeout))
    if spec.surface != job.surface:
        diffs.append(FieldDiff("surface", str(job.surface), str(spec.surface)))
    if spec.command != job.command:
        # shlex.join renders as an actual shell line, not a Python tuple repr - readable
        # for a multi-arg command.
        diffs.append(FieldDiff("command", shlex.join(job.command), shlex.join(spec.command)))
    if spec.success_exit_codes != job.success_exit_codes:
        diffs.append(FieldDiff(
            "success_exit_codes",
            ", ".join(str(c) for c in job.success_exit_codes),
            ", ".join(str(c) for c in spec.success_exit_codes),
        ))
    return tuple(diffs)


def diff_from_bundled(spec: "JobSpec", job: BundledJob) -> tuple[str, ...]:
    """Names of the fields where a registered JobSpec no longer matches its bundled
    definition, in declaration order; empty if it matches exactly. A thin wrapper over
    `diff_from_bundled_detail` so both callers stay guaranteed-identical in what counts
    as "changed" — see module docstring."""
    return tuple(fd.field for fd in diff_from_bundled_detail(spec, job))


def render_field_diffs(diffs: tuple[FieldDiff, ...], *, indent: str = "    ") -> str:
    """Shared before/after rendering for a non-empty `diff_from_bundled_detail` result -
    one `field:` sub-block per entry, in the same order, `-`/`+` prefixed so it reads as a
    diff without pulling in `difflib`. Both `_cmd_ccsched_jobs_install` and
    `check_ccsched_job_registered` call this so they render byte-identical bodies and only
    differ in the header/hint text wrapped around it."""
    lines: list[str] = []
    for d in diffs:
        lines.append(f"{indent}{d.field}:")
        lines.append(f"{indent}  - bundled: {d.bundled}")
        lines.append(f"{indent}  + current: {d.current}")
    return "\n".join(lines)
