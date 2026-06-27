"""SessionStart / UserPromptSubmit hook: deliver inter-session messages.

Builds a session context from the hook stdin payload, runs the shared
``service.deliver`` sweep, and injects a compact digest as additionalContext.
Never blocks a session: any failure degrades to empty additionalContext and is
logged via the CCST telemetry channel."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from cc_session_tools.lib.messaging import service, store
from cc_session_tools.lib.messaging.addressing import SessionContext
from cc_session_tools.lib.messaging.service import DeliverMode


def _emit(context: str, event: str) -> None:
    json.dump(
        {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}},
        sys.stdout,
    )


def _log_failure(reason: str) -> None:
    # Imported lazily so the telemetry module (and its I/O setup) is only loaded
    # on the rare failure path, keeping the hot delivery path's import cost down.
    # telemetry.log_event swallows its own I/O errors internally and never
    # raises, so no wrapper here (a swallow-only try/except is banned by the
    # repo's coding standards).
    from cccs_hooks.telemetry import TelemetryEntry, log_event
    log_event(TelemetryEntry(
        hook="messaging-deliver", event="", tool="", session_id="",
        cwd_short="", decision="annotate", cache="none",
        verdict=f"deliver-failed:{reason}", input_hash="",
    ))


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        _log_failure("bad-stdin")
        _emit("", "unknown")
        return 0
    if not isinstance(data, dict):
        # Valid JSON that is not an object (e.g. a list or scalar) would make the
        # data.get(...) calls below raise; degrade rather than crash the session.
        _log_failure("bad-stdin")
        _emit("", "unknown")
        return 0

    event = str(data.get("hook_event_name", "UserPromptSubmit"))
    mode: DeliverMode = "full" if event == "SessionStart" else "incremental"
    try:
        uuid = str(data.get("session_id", ""))
        cwd = Path(str(data.get("cwd", Path.cwd())))
        partition = store.partition_for_cwd(cwd)
        project = partition.split("/", 1)[-1]
        ctx = SessionContext(uuid=uuid, project=project, partition=partition)
        digest = service.deliver(ctx, mode=mode)
    except (OSError, ValueError) as exc:
        _log_failure(type(exc).__name__)
        _emit("", event)
        return 0

    _emit(digest, event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
