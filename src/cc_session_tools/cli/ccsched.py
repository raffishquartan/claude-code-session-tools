"""ccsched -- manage local recurring jobs reconciled on Claude Code session
start. Thin argparse layer; validation lives at this boundary, the scheduler
lib trusts validated input."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from cc_session_tools import __version__
from cc_session_tools.lib.scheduler import (
    ledger,
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
    validate_job_fields,
)
from cc_session_tools.lib.scheduler.runner import run_command


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ccsched",
        description="Manage local recurring jobs reconciled on session start.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    add_p = sub.add_parser("add", help="Register a job.")
    add_p.add_argument("--id", required=True)
    add_p.add_argument("--cadence", required=True)
    add_p.add_argument("--coalesce", default="one")
    add_p.add_argument("--catchup-window", default="7d")
    add_p.add_argument("--timeout", default="60s")
    add_surface = add_p.add_mutually_exclusive_group()
    add_surface.add_argument("--surface", dest="surface", action="store_true", default=True)
    add_surface.add_argument("--no-surface", dest="surface", action="store_false")
    # Use dest="argv" to avoid clashing with the top-level subcommand "command" dest.
    add_p.add_argument("--command", dest="argv", nargs=argparse.REMAINDER, default=[],
                       help="The argv to run (everything after --command).")

    sub.add_parser("list", help="List jobs with next_due.")

    edit_p = sub.add_parser("edit", help="Modify an existing job.")
    edit_p.add_argument("id")
    edit_p.add_argument("--cadence")
    edit_p.add_argument("--coalesce")
    edit_p.add_argument("--catchup-window")
    edit_p.add_argument("--timeout")
    edit_surface = edit_p.add_mutually_exclusive_group()
    edit_surface.add_argument("--surface", dest="surface", action="store_true", default=None)
    edit_surface.add_argument("--no-surface", dest="surface", action="store_false", default=None)
    edit_p.add_argument("--command", dest="argv", nargs=argparse.REMAINDER, default=None)

    for verb in ("enable", "disable", "remove"):
        sp = sub.add_parser(verb, help=f"{verb.capitalize()} a job.")
        sp.add_argument("id")

    run_p = sub.add_parser("run", help="Run one job now.")
    run_p.add_argument("id")
    run_p.add_argument("--force", action="store_true")

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
        spec = validate_job_fields(
            job_id=args.id, cadence=args.cadence, coalesce=args.coalesce,
            command=command, surface=args.surface, enabled=True,
            catchup_window=args.catchup_window, timeout=args.timeout,
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
    print(f"{'id':<24} {'cadence':<20} {'coalesce':<8} {'enabled':<7} {'last_success':<22} next_due")
    for s in specs:
        js = states.get(s.job_id)
        if js is not None:
            baseline_ts = state.parse_ts_or_none(js.last_success) or state.parse_ts_or_none(js.registered_at)
        else:
            baseline_ts = None
        baseline = baseline_ts if baseline_ts is not None else now
        last = (js.last_success if js else None) or "-"
        nd = next_due(parse_cadence(s.cadence), baseline, now).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"{s.job_id:<24} {s.cadence:<20} {s.coalesce.value:<8} "
              f"{str(s.enabled).lower():<7} {last:<22} {nd}")
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    specs = {s.job_id: s for s in registry.load_registry()}
    cur = specs.get(args.id)
    if cur is None:
        return _err(f"unknown job id: {args.id!r}")
    try:
        spec = validate_job_fields(
            job_id=args.id,
            cadence=args.cadence or cur.cadence,
            coalesce=(args.coalesce or cur.coalesce.value),
            command=(args.argv if args.argv is not None else list(cur.command)),
            surface=cur.surface if args.surface is None else args.surface,
            enabled=cur.enabled,
            catchup_window=args.catchup_window or cur.catchup_window,
            timeout=args.timeout or cur.timeout,
        )
    except JobValidationError as exc:
        return _err(str(exc))
    registry.replace_job(spec)
    print(f"updated {spec.job_id}")
    return 0


def _cmd_set_enabled(job_id: str, enabled: bool) -> int:
    try:
        registry.set_enabled(job_id, enabled)
    except registry.RegistryError as exc:
        return _err(str(exc))
    print(f"{'enabled' if enabled else 'disabled'} {job_id}")
    return 0


def _cmd_remove(job_id: str) -> int:
    try:
        registry.remove_job(job_id)
    except registry.RegistryError as exc:
        return _err(str(exc))
    print(f"removed {job_id}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    specs = {s.job_id: s for s in registry.load_registry()}
    spec: JobSpec | None = specs.get(args.id)
    if spec is None:
        return _err(f"unknown job id: {args.id!r}")
    outcome = run_command(spec.command, parse_duration(spec.timeout))
    now = datetime.now(timezone.utc)
    states = state.load_all_state()
    js = state.ensure_registered(states, spec.job_id, now)
    failed = outcome.timed_out or outcome.exit_code != 0
    states[spec.job_id] = state.JobState(
        registered_at=js.registered_at,
        last_success=js.last_success if failed else state.format_ts(now),
        last_attempt=state.format_ts(now),
        consecutive_failures=js.consecutive_failures + 1 if failed else 0,
    )
    state.save_all_state(states)
    ledger.record(ledger.LedgerEntry(
        job_id=spec.job_id,
        event=ledger.LedgerEvent.FAIL if failed else ledger.LedgerEvent.RUN,
        owed=1, ran=0 if failed else 1, exit_code=outcome.exit_code,
        duration_ms=outcome.duration_ms,
        error=(outcome.stderr.strip()[:200] or None) if failed else None,
    ))
    print(f"{'failed' if failed else 'ran'} {spec.job_id} (exit={outcome.exit_code})")
    return 1 if failed else 0


def _cmd_status(args: argparse.Namespace) -> int:
    rows = ledger.read_recent(job_id=args.id)
    if not rows:
        print("no recent catch-up activity")
        return 0
    for r in rows:
        print(f"{r.get('ts','')} {r.get('job_id',''):<24} {r.get('event',''):<12} "
              f"ran={r.get('ran')} exit={r.get('exit_code')}")
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    rec = reconcile.reconcile_and_launch(now=now)
    surfaced = surface.surface(session_uuid="cli-sweep")
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
