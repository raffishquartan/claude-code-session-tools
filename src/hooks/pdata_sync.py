"""SessionStart / SessionEnd hook: the automatic half of multi-laptop `ccst pdata` sync.

One module registered under two events, branching on `hook_event_name` inside `main()` - the
same shape `catchup.py` uses for its own SessionStart/UserPromptSubmit pair.

- **SessionStart** rehydrates this project from `.pdata-db-dump/latest.sql` if the dump dominates
  local, after the spec's occupancy gate ("Process safety"): if another live `claude` session is
  already working in this project - excluding this hook's own launching process, resolved via
  `occupancy.launching_claude_pid` (not bare `os.getppid()`, which is an `sh -c` wrapper rather
  than the `claude` process itself whenever `/bin/sh` is dash) - it skips entirely rather than
  change that session's data mid-task.
- **SessionEnd** publishes a fresh dump if local has writes the published dump lacks.

Neither direction ever blocks or crashes a session. Any unexpected failure degrades to a logged
telemetry warning plus empty output and exit 0, matching `catchup.py`'s own top-level handler.
The caught set is `(OSError, ValueError, sqlite3.Error)`, confirmed against this module's actual
call surface rather than copied on faith: `occupancy.is_occupied` already swallows its own
`subprocess.SubprocessError`/`OSError` into a fail-safe `True` (so nothing subprocess-shaped
escapes it), `machine_identity.resolve()` never raises - a corrupt or wrongly-shaped
machine-identity store degrades to the same unconfirmed-hostname fallback a missing store
already gets - and `store.db_path`/`rehydrate`/`dump` raise `ValueError`, `OSError` and
`sqlite3.Error` between them. Nothing here raises anything else.

`SessionEnd` hooks share a 1.5-second budget across all matching hooks unless an explicit
`timeout` is set, so `hooks-bundle.json` gives this one 10s - `on_session_end` does real I/O
(a SQLite read plus a full DB serialize on `dump.write_latest`).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

from cc_session_tools.lib import machine_identity, occupancy
from cc_session_tools.lib.pdata import (
    dump,
    init_paths,
    rehydrate,
    repository,
    store,
    sync_notify,
    vector_clock_store,
)
from cc_session_tools.lib.scheduler import ledger
from hooks.telemetry import TelemetryEntry, log_event

logger = logging.getLogger(__name__)


def _emit(message: str | None, event: str) -> None:
    """Always emit *something*, matching catchup.py's `_emit`. `additionalContext` alone only
    ever reaches the model; `systemMessage` is what Claude Code prints to the user's terminal, so
    it is added whenever there is a message - which `on_session_start` now always supplies for a
    pdata-migrated project, on Chris's explicit request to always see that the hook ran and what
    it decided, rather than trust silence to mean "nothing happened".

    `hookSpecificOutput` is omitted for `SessionEnd`: unlike SessionStart (which has an
    `additionalContext`-bearing shape in Claude Code's output schema), SessionEnd has no
    `hookSpecificOutput` variant at all - the discriminated union simply doesn't define one,
    since the session is already ending and there is no conversation left for
    `additionalContext` to reach. Emitting one there - regardless of its contents - fails the
    host's JSON validation and surfaces as a hook-failure error to the user. `on_session_end`
    never has a message to show anyway (see its own docstring), so this costs nothing."""
    payload: dict[str, object] = {}
    if event != "SessionEnd":
        payload["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": message or ""}
    if message:
        payload["systemMessage"] = message
    json.dump(payload, sys.stdout)


def _log_failure(reason: str) -> None:
    # Explicit hooks_dir= for the same reason catchup.py passes one: telemetry.log_event does not
    # read CCCS_HOOKS_DIR itself, so without this a test would write into the developer's real
    # ledger.
    log_event(
        TelemetryEntry(
            hook="pdata-sync", event="", tool="", session_id="", cwd_short="",
            decision="annotate", cache="none", verdict=f"pdata-sync-failed:{reason}",
            input_hash="",
        ),
        hooks_dir=ledger._hooks_dir(),
    )


def _resolve_project(cwd: str) -> tuple[str, Path] | None:
    """`(project, project_root)` for a cwd that is a real, pdata-migrated project, else None.

    Neither hook event's stdin carries a project name - only `cwd` - and this hook runs on every
    session start regardless of project, so the "not a pdata project" case must stay cheap: both
    rejections below happen before any occupancy check, rehydrate or dump.
    """
    if not cwd:
        return None
    cwd_path = Path(cwd).resolve()
    if cwd_path.parent != init_paths.default_projects_root().resolve():
        # Not even ~/cc/<project>-shaped. A session in a git repo, /tmp, or a cc-sessions/<tag>/
        # subdirectory lands here.
        return None
    project = cwd_path.name
    if not store.db_path(project).exists():
        # Under ~/cc but never `ccst pdata init`'d - the established "is this project
        # pdata-migrated at all" test used throughout service.py/session_output.py.
        return None
    return project, store.project_root(project)


def on_session_start(cwd: str, *, session_pid: int | None) -> str | None:
    """Rehydrate this project if the published dump dominates local. Returns the message to show
    the starting session, or None when this session's cwd isn't a pdata-migrated project at all
    (the "not even worth mentioning" case, since this hook fires on every session start
    regardless of project).

    For an actual pdata project, always returns a message, however unremarkable the outcome -
    Chris asked to always be able to see that the hook ran and what it did (rather than trusting
    silence to mean "nothing to report"), so even the common NO_OP case gets one line.

    `session_pid` is None when `occupancy.launching_claude_pid` couldn't identify the launching
    `claude` process (see its own docstring) - `is_occupied()` then excludes nothing, which is
    the same conservative direction every other unresolvable case in this module already takes."""
    resolved = _resolve_project(cwd)
    if resolved is None:
        return None
    project, project_root = resolved

    if occupancy.is_occupied(project_root, exclude_pid=session_pid):
        # Another live session in this project - even on this same laptop, e.g. a second terminal
        # tab - is mid-task. Skip; the next trigger (SessionEnd, the hourly job, or the next
        # session start) retries.
        return f"[pdata-sync] {project}: skipped - already open in another Claude Code session"

    result = rehydrate.rehydrate(project)
    if result.outcome is rehydrate.RehydrateOutcome.FAST_FORWARDED:
        if result.dumped_at is not None:
            when = dump.format_dumped_at(result.dumped_at)
        else:
            # Same fallback init_service.py's _format_published_at uses for the identical
            # "dump written before dumped_at existed" edge case (currently unreachable - every
            # dump this feature has ever written already carries dumped_at - but the two sibling
            # formatters should render it the same way rather than diverge on a case neither can
            # actually hit yet). mtime reflects local sync-settle time, not the source machine's
            # actual publish time, but it beats an unhelpful "unknown" for a cosmetic detail.
            latest_path = project_root / ".pdata-db-dump" / "latest.sql"
            when = dump.format_dumped_at(int(latest_path.stat().st_mtime))
        # The design's required content is "machine + timestamp it rehydrated from"; this exact
        # wording is the plan's own quoted deliverable, so it is reproduced verbatim rather than
        # wrapped in this module's `[pdata-sync]` prefix like the conflict messages below.
        return (
            f"Re-hydrating project pdata DB based on updates made on "
            f"`{result.from_machine}` at `{when}`"
        )
    if result.outcome in (
        rehydrate.RehydrateOutcome.FORK, rehydrate.RehydrateOutcome.CHECKSUM_INVALID,
    ):
        detail = rehydrate.conflict_detail(result, project=project)
        # Both channels, deliberately. notify_conflict covers the *next* session and the hourly
        # digest; the returned systemMessage covers the session that just hit this, which must
        # not have to wait for a future digest to learn about its own conflict (see
        # sync_notify.py's module docstring, which names this hook for exactly that reason).
        sync_notify.notify_conflict(project, outcome=result.outcome.value, detail=detail)
        return f"[pdata-sync] {project}: {detail}"
    if result.outcome is rehydrate.RehydrateOutcome.DEFERRED:
        # Another writer holds the local db's lock right now - transient, the next trigger
        # retries. Not actionable, but still worth a line so a stuck-looking rehydrate is visible
        # rather than indistinguishable from "ran fine, nothing to do".
        return f"[pdata-sync] {project}: skipped for now (local database busy) - will retry automatically"
    # NO_OP: local is already at or ahead of the dump - the common case on nearly every session
    # start. Not actionable, but still reported so the hook having run is never in question.
    return f"[pdata-sync] {project}: up to date, nothing to sync"


def on_session_end(cwd: str) -> None:
    """Publish a fresh dump if local has writes the published dump lacks.

    No occupancy check, per the spec's "Process safety": dumping is a read-only copy, which
    SQLite already makes safe against concurrent access - the gate exists only for the
    content-replacing rehydrate direction.

    Silent in every case, including success: the session is ending, so there is no reader for a
    systemMessage, and SessionEnd's tight time budget favours doing the minimum. A refusal still
    goes to sync_notify, which is the only way the user ever learns about a conflict discovered
    here."""
    resolved = _resolve_project(cwd)
    if resolved is None:
        return
    project, project_root = resolved

    conn = repository.connect(project)
    try:
        local_vector = vector_clock_store.read_vector(conn)
        existing = dump.read_latest(project_root)
        comparison = dump.decide_publish(local_vector=local_vector, existing=existing)
        if comparison is not None:
            sync_notify.notify_conflict(
                project, outcome=comparison.value, detail=dump.refusal_detail(project),
            )
            return
        dump.write_latest(
            conn,
            project_root=project_root,
            machine_id=machine_identity.resolve().machine_id,
            vector=local_vector,
        )
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        _log_failure("bad-stdin")
        _emit(None, "SessionStart")
        return 0
    event = str(data.get("hook_event_name", ""))
    cwd = str(data.get("cwd", ""))
    message: str | None = None
    try:
        if event == "SessionStart":
            # os.getppid() is this hook subprocess's immediate parent - but that's an `sh -c`
            # wrapper, not the `claude` process itself, whenever `/bin/sh` is dash rather than
            # bash (confirmed empirically, see launching_claude_pid's docstring). Climbing from
            # there to the nearest actual `claude` ancestor is what makes the spec's "SessionStart
            # excludes its own just-launched process" exclusion actually match anything.
            message = on_session_start(
                cwd, session_pid=occupancy.launching_claude_pid(os.getppid()),
            )
        elif event == "SessionEnd":
            on_session_end(cwd)
        # Any other event: no-op. Only the two above are ever registered; this arm exists for the
        # same defensive reason catchup.py's own event branching does.
    except (OSError, ValueError, sqlite3.Error) as exc:
        _log_failure(type(exc).__name__)
        _emit(None, event)
        return 0
    _emit(message, event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
