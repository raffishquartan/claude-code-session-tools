"""Hook-fire telemetry: writes one row per fire into telemetry.db
(telemetry_events table).

All bash hooks call this module via:
    echo "$INPUT" | python3 -m cccs_hooks.telemetry log --hook NAME ...

Never raises — write failures are logged to stderr and silently suppressed so
a telemetry error never blocks a hook.

Storage lives at CCCS_HOOKS_DIR/telemetry.db (default:
cc_session_tools.lib.paths.data_home()); see lib.telemetry_store for the
schema and path-resolution logic shared with telemetry_trim, telemetry_query,
and lib.scheduler.ledger. Explicit pruning: use ``ccst telemetry trim``.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import sys
from pathlib import Path
from typing import Literal

from cc_session_tools.lib import telemetry_store


@dataclasses.dataclass(frozen=True, slots=True)
class TelemetryEntry:
    hook: str
    event: str
    tool: str
    session_id: str
    cwd_short: str
    decision: Literal["allow", "deny", "annotate"]
    cache: Literal["hit", "miss", "none"]
    verdict: str
    input_hash: str


def log_event(entry: TelemetryEntry, *, hooks_dir: Path | None = None) -> None:
    """Insert one row into telemetry_events. Never raises."""
    try:
        conn = telemetry_store.connect(hooks_dir)
        try:
            conn.execute(
                "INSERT INTO telemetry_events "
                "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    telemetry_store.now_iso(), entry.hook, entry.event, entry.tool,
                    entry.session_id, entry.cwd_short, entry.decision, entry.cache,
                    entry.verdict, entry.input_hash,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as e:
        print(f"[telemetry-warn] log write failed: {e}", file=sys.stderr)


def _shorten_cwd(cwd: str) -> str:
    """Keep last 2 path components to limit PII exposure in the log."""
    parts = Path(cwd).parts
    return "/".join(parts[-2:]) if len(parts) >= 2 else cwd


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cccs_hooks.telemetry")
    sub = p.add_subparsers(dest="cmd")
    log_p = sub.add_parser("log")
    log_p.add_argument("--hook", required=True)
    log_p.add_argument("--event", required=True)
    log_p.add_argument("--decision", required=True)
    log_p.add_argument("--cache", default="none")
    log_p.add_argument("--verdict", default="")
    log_p.add_argument("--input-hash", default="")
    args = p.parse_args(argv)
    if args.cmd != "log":
        p.print_help()
        return 1
    raw = sys.stdin.read()
    try:
        data: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    entry = TelemetryEntry(
        hook=args.hook,
        event=args.event,
        tool=str(data.get("tool_name", "")),
        session_id=str(data.get("session_id", "")),
        cwd_short=_shorten_cwd(str(data.get("cwd", ""))),
        decision=args.decision,
        cache=args.cache,
        verdict=args.verdict,
        input_hash=args.input_hash,
    )
    log_event(entry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
