"""SessionStart / UserPromptSubmit hook: reconcile + launch scheduled jobs
detached, then surface (reap) completed runs as a catch-up digest.

Does only the cheap part on the critical path — reconcile (what is owed?) and
LAUNCH detached workers, then surface ledger-since-cursor entries. Job commands
run off the critical path in `ccsched _run-job` workers. Never blocks a session:
any failure degrades to an empty additionalContext and is logged to telemetry
(§15). Throttles reconcile on UserPromptSubmit so sub-daily cadences fire during
a long session without re-reconciling on every keypress."""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from cc_session_tools.lib.scheduler import cursor, ledger, reconcile, surface, throttle
from cc_session_tools.lib.scheduler.digest import format_digest
from cccs_hooks.telemetry import TelemetryEntry, log_event

logger = logging.getLogger(__name__)

_RECONCILE_THROTTLE = timedelta(seconds=60)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(context: str, event: str) -> None:
    json.dump(
        {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}},
        sys.stdout,
    )


def _log_failure(reason: str) -> None:
    # Route through ledger._hooks_dir() so CCCS_HOOKS_DIR is honoured. telemetry.log_event
    # does NOT read CCCS_HOOKS_DIR itself (only telemetry.main() does), so without an
    # explicit hooks_dir= this would write to the real ~/.cache/claude/logs/fires.jsonl even
    # under tests that set CCCS_HOOKS_DIR - polluting the user's real ledger (§15).
    log_event(
        TelemetryEntry(
            hook="catchup", event="", tool="", session_id="", cwd_short="",
            decision="annotate", cache="none", verdict=f"catchup-failed:{reason}",
            input_hash="",
        ),
        hooks_dir=ledger._hooks_dir(),
    )


def _should_reconcile(event: str, uuid: str, now: datetime) -> bool:
    """SessionStart always reconciles; UserPromptSubmit reconciles at most once
    per throttle window per session (§13)."""
    if event == "SessionStart":
        return True
    last = throttle.read_last_reconciled(uuid)
    return last is None or now - last >= _RECONCILE_THROTTLE


def _stamp_reconcile(uuid: str, now: datetime) -> None:
    throttle.stamp_reconciled(uuid, now)


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        _log_failure("bad-stdin")
        _emit("", "SessionStart")
        return 0
    event = str(data.get("hook_event_name", "SessionStart"))
    uuid = str(data.get("session_id", "unknown"))
    now = _now()
    parse_error: str | None = None
    try:
        # Seed a brand-new session's cursor to the current end of the ledger before
        # reconcile writes anything, so its first digest reflects only activity from
        # this point forward - not weeks of pre-existing history (§9.3 fix).
        cursor.seed_new_session(uuid)
        if _should_reconcile(event, uuid, now):
            rec = reconcile.reconcile_and_launch(now=now, spawn=reconcile.spawn_detached)
            parse_error = rec.parse_error
            _stamp_reconcile(uuid, now)
        if parse_error is not None:
            # Registry is unparseable; skip surface (it would also fail) and emit the
            # parse-error digest immediately so the user sees the warning.
            _emit(format_digest([], parse_error=parse_error), event)
            return 0
        surfaced = surface.surface(session_uuid=uuid, now=now)
        digest = format_digest(surfaced.reports, parse_error=None)
    except (OSError, ValueError, sqlite3.Error) as exc:
        _log_failure(type(exc).__name__)
        _emit("", event)
        return 0
    _emit(digest, event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
