"""ccsched -- manage local recurring jobs reconciled on Claude Code session
start. Thin argparse layer; validation lives at this boundary, the scheduler
lib trusts validated input."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from cc_session_tools import __version__
from cc_session_tools.lib.scheduler import (
    cursor,
    ledger,
    notify,
    reconcile,
    registry,
    state,
    surface,
    worker,
)
from cc_session_tools.lib.scheduler.cadence import parse_cadence
from cc_session_tools.lib.scheduler.digest import format_digest
from cc_session_tools.lib.scheduler.due import next_due
from cc_session_tools.lib.scheduler.duration import parse_duration
from cc_session_tools.lib.scheduler.jobspec import (
    JobSpec,
    JobValidationError,
    parse_success_exit_codes,
    validate_job_fields,
)
from cc_session_tools.lib.scheduler.runner import run_command


# Shared option descriptions for `add`/`edit` (siblings that validate the same
# fields via jobspec.validate_job_fields) - defined once here so their help
# text can't drift between the two subcommands. Each is phrased as a
# self-contained sentence; add/edit append their own default/omission note.
_CADENCE_HELP = (
    "Schedule expression. Forms: every:<dur> | every:<dur>@from=YYYY-MM-DD | "
    "daily@HH:MM | weekly:<dow>@HH:MM | monthly:<dom>@HH:MM | "
    "monthly:<dow>#<n>@HH:MM. <dur> is <int><s|m|h|d|w> (e.g. 6h, 2d); <dow> "
    "is mon..sun; <n> is 1..5 or 'last'."
)
_COALESCE_HELP = (
    "How catch-up runs coalesce when a job is overdue by more than one missed "
    "scheduled instant: 'one' = run once for the whole backlog; 'each' = run "
    "once per missed instant."
)
_CATCHUP_WINDOW_HELP = (
    "How far back a missed run still counts as owed. Instants older than this "
    "are skipped rather than run. Format: <int><s|m|h|d|w>, e.g. 6h, 2d, 1w."
)
_TIMEOUT_HELP = (
    "Kill the job's command if it hasn't finished after this long. Format: "
    "<int><s|m|h|d|w>, e.g. 30s, 5m."
)
_SUCCESS_EXIT_CODES_HELP = (
    "Comma-separated exit codes that count as a successful run, not a "
    "failure. Use e.g. '0,1' for a check-style command whose exit 1 means "
    "'found something', not 'crashed' - such a code never counts toward "
    "auto-suspend, and its stdout is surfaced in the session-start digest as "
    "findings."
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ccsched",
        description="Manage local recurring jobs reconciled on session start.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    add_p = sub.add_parser("add", help="Register a job.")
    add_p.add_argument("--id", required=True, help="Job id, lowercase kebab-case [a-z0-9-].")
    add_p.add_argument("--cadence", required=True, help=_CADENCE_HELP)
    add_p.add_argument("--coalesce", default="one",
                        help=_COALESCE_HELP + " Choices: one, each (default: one).")
    add_p.add_argument("--catchup-window", default="7d",
                        help=_CATCHUP_WINDOW_HELP + " (default: 7d)")
    add_p.add_argument("--timeout", default="120s",
                        help=_TIMEOUT_HELP + " (default: 120s)")
    add_p.add_argument(
        "--success-exit-codes", default="0",
        help=_SUCCESS_EXIT_CODES_HELP + " (default: 0)",
    )
    add_surface = add_p.add_mutually_exclusive_group()
    add_surface.add_argument(
        "--surface", dest="surface", action="store_true", default=True,
        help="Include this job's output in the session-start digest when it runs (default).",
    )
    add_surface.add_argument(
        "--no-surface", dest="surface", action="store_false",
        help="Run silently; do not surface this job's output in the session-start digest.",
    )
    # Use dest="argv" to avoid clashing with the top-level subcommand "command" dest.
    add_p.add_argument("--command", dest="argv", nargs=argparse.REMAINDER, default=[],
                       help="The argv to run (everything after --command); required, "
                            "at least one element.")

    sub.add_parser("list", help="List jobs with next_due.")

    show_p = sub.add_parser("show", help="Show one job's full spec and state.")
    show_p.add_argument("id", help="Job id to show.")

    edit_p = sub.add_parser("edit", help="Modify an existing job.")
    edit_p.add_argument("id", help="Job id to edit.")
    edit_p.add_argument("--cadence",
                         help=_CADENCE_HELP + " Omit to keep the job's current cadence.")
    edit_p.add_argument("--coalesce",
                         help=_COALESCE_HELP + " Choices: one, each. Omit to keep the "
                              "job's current coalesce mode.")
    edit_p.add_argument("--catchup-window",
                         help=_CATCHUP_WINDOW_HELP + " Omit to keep the job's current "
                              "catchup_window.")
    edit_p.add_argument("--timeout",
                         help=_TIMEOUT_HELP + " Omit to keep the job's current timeout.")
    edit_p.add_argument(
        "--success-exit-codes", default=None,
        help=_SUCCESS_EXIT_CODES_HELP + " Omit to keep the job's current success_exit_codes.",
    )
    edit_surface = edit_p.add_mutually_exclusive_group()
    edit_surface.add_argument(
        "--surface", dest="surface", action="store_true", default=None,
        help="Include this job's output in the session-start digest when it runs.",
    )
    edit_surface.add_argument(
        "--no-surface", dest="surface", action="store_false", default=None,
        help="Run silently; do not surface this job's output in the session-start digest. "
             "Omit both --surface and --no-surface to keep the job's current setting.",
    )
    edit_p.add_argument("--command", dest="argv", nargs=argparse.REMAINDER, default=None,
                         help="New argv to run (everything after --command); omit to keep "
                              "the job's current command.")

    for verb in ("enable", "disable", "remove"):
        sp = sub.add_parser(verb, help=f"{verb.capitalize()} a job.")
        sp.add_argument("id")

    run_p = sub.add_parser("run", help="Run one job now.")
    run_p.add_argument("id")

    status_p = sub.add_parser("status", help="Recent ledger entries.")
    status_p.add_argument("id", nargs="?", default=None)

    sub.add_parser("sweep", help="Run reconcile+launch+surface now.")

    runjob_p = sub.add_parser("_run-job", help="(internal) detached worker; not for direct use.")
    runjob_p.add_argument("id")
    runjob_p.add_argument("--instants", type=int, default=1)
    return p


def _err(msg: str) -> int:
    print(f"ccsched: {msg}", file=sys.stderr)
    return 2


def _cmd_add(args: argparse.Namespace) -> int:
    command = list(args.argv) if args.argv else []
    try:
        success_exit_codes = parse_success_exit_codes(args.success_exit_codes)
        spec = validate_job_fields(
            job_id=args.id, cadence=args.cadence, coalesce=args.coalesce,
            command=command, surface=args.surface, enabled=True,
            catchup_window=args.catchup_window, timeout=args.timeout,
            success_exit_codes=success_exit_codes,
        )
    except JobValidationError as exc:
        return _err(str(exc))
    try:
        registry.add_job(spec)
    except registry.RegistryError as exc:
        return _err(str(exc))
    print(f"added {spec.job_id}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    specs = registry.load_registry()
    states = state.load_all_state()
    now = datetime.now(timezone.utc)

    headers = ("id", "cadence", "coalesce", "enabled", "last_success", "next_due")
    rows = []
    for s in specs:
        js = states.get(s.job_id)
        if js is not None:
            baseline_ts = state.parse_ts_or_none(js.last_success) or state.parse_ts_or_none(js.registered_at)
        else:
            baseline_ts = None
        baseline = baseline_ts if baseline_ts is not None else now
        last = (js.last_success if js else None) or "-"
        nd = next_due(parse_cadence(s.cadence), baseline, now).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append((s.job_id, s.cadence, s.coalesce.value, str(s.enabled).lower(), last, nd))

    widths = [
        max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
        for i, header in enumerate(headers)
    ]
    print("  ".join(header.ljust(width) for header, width in zip(headers, widths[:-1])) + "  " + headers[-1])
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths[:-1])) + "  " + row[-1])
    return 0


def _print_job(spec: JobSpec) -> None:
    """Print one job's full spec + runtime state, in `id: value` form. Shared
    by `show` and `edit` (the printed-after-edit definition) so both stay in
    sync with one field list."""
    js = state.get_state(spec.job_id)
    now = datetime.now(timezone.utc)
    baseline_ts = None
    if js is not None:
        baseline_ts = state.parse_ts_or_none(js.last_success) or state.parse_ts_or_none(js.registered_at)
    baseline = baseline_ts if baseline_ts is not None else now
    nd = next_due(parse_cadence(spec.cadence), baseline, now).strftime("%Y-%m-%dT%H:%M:%SZ")

    in_flight = "-"
    if js is not None and js.in_flight is not None:
        in_flight = (f"pid={js.in_flight.pid} started_at={js.in_flight.started_at} "
                     f"instants={js.in_flight.instants}")

    fields = [
        ("id", spec.job_id),
        ("cadence", spec.cadence),
        ("coalesce", spec.coalesce.value),
        ("enabled", str(spec.enabled).lower()),
        ("surface", str(spec.surface).lower()),
        ("catchup_window", spec.catchup_window),
        ("timeout", spec.timeout),
        ("success_exit_codes", ",".join(str(c) for c in spec.success_exit_codes)),
        ("command", " ".join(spec.command)),
        ("next_due", nd),
        ("registered_at", js.registered_at if js else "-"),
        ("last_success", (js.last_success if js else None) or "-"),
        ("last_attempt", (js.last_attempt if js else None) or "-"),
        ("consecutive_failures", str(js.consecutive_failures) if js else "-"),
        ("suspended", str(js.suspended).lower() if js else "-"),
        ("in_flight", in_flight),
    ]
    width = max(len(label) for label, _ in fields)
    for label, value in fields:
        print(f"{label + ':':<{width + 1}} {value}")


def _cmd_show(args: argparse.Namespace) -> int:
    specs = {s.job_id: s for s in registry.load_registry()}
    spec = specs.get(args.id)
    if spec is None:
        return _err(f"unknown job id: {args.id!r}")
    _print_job(spec)
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    specs = {s.job_id: s for s in registry.load_registry()}
    cur = specs.get(args.id)
    if cur is None:
        return _err(f"unknown job id: {args.id!r}")
    try:
        success_exit_codes = (
            parse_success_exit_codes(args.success_exit_codes)
            if args.success_exit_codes is not None
            else cur.success_exit_codes
        )
        spec = validate_job_fields(
            job_id=args.id,
            cadence=args.cadence or cur.cadence,
            coalesce=(args.coalesce or cur.coalesce.value),
            command=(args.argv if args.argv is not None else list(cur.command)),
            surface=cur.surface if args.surface is None else args.surface,
            enabled=cur.enabled,
            catchup_window=args.catchup_window or cur.catchup_window,
            timeout=args.timeout or cur.timeout,
            success_exit_codes=success_exit_codes,
        )
    except JobValidationError as exc:
        return _err(str(exc))
    registry.replace_job(spec)
    print(f"updated {spec.job_id}")
    _print_job(spec)
    return 0


def _cmd_set_enabled(job_id: str, enabled: bool) -> int:
    try:
        registry.set_enabled(job_id, enabled)
    except registry.RegistryError as exc:
        return _err(str(exc))
    if enabled:
        state.clear_suspended(job_id)
    print(f"{'enabled' if enabled else 'disabled'} {job_id}")
    return 0


def _cmd_remove(job_id: str) -> int:
    try:
        registry.remove_job(job_id)
    except registry.RegistryError as exc:
        return _err(str(exc))
    print(f"removed {job_id}")
    return 0


def _cmd_run(
    args: argparse.Namespace, *, notify_push: worker.NotifyPush = notify.push_outcome,
) -> int:
    specs = {s.job_id: s for s in registry.load_registry()}
    spec: JobSpec | None = specs.get(args.id)
    if spec is None:
        return _err(f"unknown job id: {args.id!r}")
    outcome = run_command(spec.command, parse_duration(spec.timeout))
    now = datetime.now(timezone.utc)
    attempt_ts = state.format_ts(now)
    state.ensure_registered_db(spec.job_id, now)
    # Manual runs always use record_manual_failure/record_success, not
    # worker.py's suspend-threshold-aware record_failure - `ccsched run` never
    # auto-suspends a job, unlike the scheduled `_run-job` path.
    capture = worker.classify_outcome(spec, outcome, notify_push=notify_push)
    if capture.crashed:
        new_consecutive = state.record_manual_failure(spec.job_id, attempt_ts=attempt_ts)
    else:
        state.record_success(spec.job_id, new_success=attempt_ts, attempt_ts=attempt_ts)
        new_consecutive = 0
    ledger.record(ledger.LedgerEntry(
        job_id=spec.job_id, event=capture.event, owed=1, ran=0 if capture.crashed else 1,
        exit_code=outcome.exit_code, duration_ms=outcome.duration_ms, error=capture.detail,
        consecutive_failures=new_consecutive if capture.crashed else 0,
    ))
    print(f"{'failed' if capture.crashed else 'ran'} {spec.job_id} (exit={outcome.exit_code})")
    return 1 if capture.crashed else 0


def _cmd_status(args: argparse.Namespace) -> int:
    rows = ledger.read_recent(job_id=args.id)
    if not rows:
        print("no recent catch-up activity")
        return 0
    for r in rows:
        print(f"{r.get('ts','')} {r.get('job_id',''):<24} {r.get('event',''):<12} "
              f"ran={r.get('ran')} exit={r.get('exit_code')}")
        error = r.get("error")
        if error:
            print(f"    {error}")
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    # Seed the fixed "cli-sweep" cursor before reconcile writes anything, so the very
    # first `ccsched sweep` invocation on a machine doesn't replay the entire
    # pre-existing ledger history (same fix as the catchup hook, §9.3).
    cursor.seed_new_session("cli-sweep")
    rec = reconcile.reconcile_and_launch(now=now)
    surfaced = surface.surface(session_uuid="cli-sweep", now=now)
    digest = format_digest(surfaced.reports, parse_error=rec.parse_error)
    print(digest or "nothing surfaced")
    return 0


def _cmd_run_job(args: argparse.Namespace) -> int:
    try:
        worker.run_job(args.id, instants=args.instants, now=datetime.now(timezone.utc))
    except worker.UnknownJob as exc:
        return _err(str(exc))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "add":
        return _cmd_add(args)
    if args.command == "list":
        return _cmd_list(args)
    if args.command == "show":
        return _cmd_show(args)
    if args.command == "edit":
        return _cmd_edit(args)
    if args.command == "enable":
        return _cmd_set_enabled(args.id, True)
    if args.command == "disable":
        return _cmd_set_enabled(args.id, False)
    if args.command == "remove":
        return _cmd_remove(args.id)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "sweep":
        return _cmd_sweep(args)
    if args.command == "_run-job":
        return _cmd_run_job(args)
    _build_parser().print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
