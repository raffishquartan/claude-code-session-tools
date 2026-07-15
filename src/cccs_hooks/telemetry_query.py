"""CLI for ad hoc telemetry lookups: ccst telemetry query.

Answers "what fired recently" / "any failures in the last hour" against
telemetry_events without grepping a raw file (or, post-migration, without a
raw file to grep at all). Scoped to telemetry_events (the generic
PreToolUse/bash-security-review hook-fire family) — catch-up/job-run events
already have a dedicated, typed reader in ``ccsched status``.

Designed to be invoked via ``ccst telemetry query``; can also run directly as
``python -m cccs_hooks.telemetry_query``.
"""
from __future__ import annotations

import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

from cc_session_tools.lib import telemetry_store
from cc_session_tools.lib.scheduler.duration import DurationError, parse_duration

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_DEFAULT_LIMIT = 50


def query_events(
    *,
    hook: str | None = None,
    decision: str | None = None,
    verdict: str | None = None,
    since_ts: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    hooks_dir: Path | None = None,
) -> list[sqlite3.Row]:
    """Rows from telemetry_events matching the given filters, newest-first.

    verdict is an exact match (e.g. "safe", "suspicious", "dangerous") — added per
    ccst-migration-and-cli-update-spec.md Section 5.1, which requires filters on
    "hook name, verdict, time range, at minimum"; decision (allow/deny/annotate) and
    verdict are distinct columns and decision cannot substitute for this filter.
    """
    conn = telemetry_store.connect(hooks_dir)
    try:
        clauses: list[str] = []
        params: list[object] = []
        if hook is not None:
            clauses.append("hook = ?")
            params.append(hook)
        if decision is not None:
            clauses.append("decision = ?")
            params.append(decision)
        if verdict is not None:
            clauses.append("verdict = ?")
            params.append(verdict)
        if since_ts is not None:
            clauses.append("ts >= ?")
            params.append(since_ts)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return conn.execute(
            f"SELECT * FROM telemetry_events {where} ORDER BY id DESC LIMIT ?", params
        ).fetchall()
    finally:
        conn.close()


def _format_row(row: sqlite3.Row) -> str:
    return (
        f"{row['ts']}  {row['hook']:<26} {row['event']:<16} {row['decision']:<9} "
        f"cache={row['cache']:<4} tool={row['tool']:<10} verdict={row['verdict']}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="ccst telemetry query",
        description="Query telemetry.db's telemetry_events table (newest-first).",
    )
    p.add_argument("--hook", default=None, metavar="NAME", help="Filter by exact hook name")
    p.add_argument(
        "--decision", default=None, choices=["allow", "deny", "annotate"],
        help="Filter by decision",
    )
    p.add_argument(
        "--verdict", default=None, metavar="VERDICT",
        help="Filter by exact verdict text (e.g. safe, suspicious, dangerous)",
    )
    p.add_argument(
        "--since", default=None, metavar="DURATION",
        help="Only events at or after now-DURATION, e.g. 1h, 30m, 2d, 1w",
    )
    p.add_argument(
        "--limit", type=int, default=_DEFAULT_LIMIT, metavar="N",
        help=f"Max rows to print (default: {_DEFAULT_LIMIT})",
    )
    p.add_argument(
        "--hooks-dir", default=None, metavar="DIR",
        help="telemetry.db directory (default: CCCS_HOOKS_DIR or ~/.local/share/claude/)",
    )
    args = p.parse_args(argv)

    since_ts: str | None = None
    if args.since is not None:
        try:
            delta = parse_duration(args.since)
        except DurationError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        since_ts = (datetime.datetime.now(datetime.timezone.utc) - delta).strftime(_TS_FMT)

    hooks_dir = Path(args.hooks_dir) if args.hooks_dir else None
    rows = query_events(
        hook=args.hook, decision=args.decision, verdict=args.verdict, since_ts=since_ts,
        limit=args.limit, hooks_dir=hooks_dir,
    )

    if not rows:
        print("No matching telemetry events.")
        return 0

    for row in rows:
        print(_format_row(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
