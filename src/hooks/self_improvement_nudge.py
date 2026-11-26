"""SessionStart hook: nudge a periodic self-improvement review when the signals warrant one.

Shows one line, to the user as ``systemMessage`` and to the assistant as ``additionalContext``,
at most once per local calendar day, and nothing otherwise, including on an install that has
never used the loop. Stays silent when the config
repository's own bash nudge is registered in the same settings file. Reads files only (plus
the ``last-nudge`` stamp it writes) and always exits 0: it is advisory and must never fail a
session start. See ``cc_session_tools.lib.self_improvement.nudge`` for the rule.
"""
from __future__ import annotations

import datetime as dt
import json
import sys

from cc_session_tools.lib import paths
from cc_session_tools.lib.self_improvement import nudge, state


def _log_failure(reason: str) -> None:
    from hooks.telemetry import TelemetryEntry, log_event
    log_event(TelemetryEntry(
        hook="self-improvement-nudge", event="SessionStart", tool="", session_id="",
        cwd_short="", decision="annotate", cache="none",
        verdict=f"self-improvement-nudge-failed:{reason}", input_hash="",
    ))


def main(argv: list[str] | None = None) -> int:
    # The payload is not needed: the decision depends only on the state files. Drain stdin so
    # the caller never blocks on a full pipe.
    sys.stdin.read()
    try:
        text = nudge.session_start_message(
            state.state_dir(), paths.claude_settings_path(), dt.date.today()
        )
    except Exception as exc:  # noqa: BLE001 - advisory hook: never fail a session start
        _log_failure(type(exc).__name__)
        return 0
    if text is not None:
        json.dump(
            {
                "systemMessage": text,
                "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text},
            },
            sys.stdout,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
