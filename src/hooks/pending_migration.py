"""SessionStart hook: surface unmigrated legacy data-store files.

The 1.0.0 data-store restructure moved ccmsg/ccsched/sessions/telemetry from
flat files into SQLite stores under ~/.local/share/claude/, via one-shot
migration scripts (`ccst migrate all`). Those scripts cannot run
automatically — their delete-old-files step is statically blocked by the
bash-hard-deny hook when invoked from inside a Claude Code session, by
design — so this hook only detects and surfaces the gap, via
doctor.check_pending_data_store_migration, filtered through the same
doctor-mutes store `ccst doctor --mute` uses (so an operator who has
deliberately deferred migration doesn't get renagged every session).

Never blocks a session: any failure degrades to empty additionalContext and
is logged via the CCST telemetry channel, matching messaging_deliver.py /
catchup.py.
"""
from __future__ import annotations

import json
import sqlite3
import sys

from cc_session_tools.lib import doctor_mutes
from cc_session_tools.lib.doctor import (
    CheckResult,
    LegacyMigrationPaths,
    Status,
    check_pending_data_store_migration,
    filter_unmuted_issues,
)


def _emit(context: str, event: str) -> None:
    json.dump(
        {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}},
        sys.stdout,
    )


def _log_failure(reason: str) -> None:
    from hooks.telemetry import TelemetryEntry, log_event
    log_event(TelemetryEntry(
        hook="pending-migration", event="", tool="", session_id="",
        cwd_short="", decision="annotate", cache="none",
        verdict=f"pending-migration-failed:{reason}", input_hash="",
    ))


def _default_legacy_paths() -> LegacyMigrationPaths:
    from cc_session_tools.cli.migrate_ccmsg import DEFAULT_OLD_ROOT as ccmsg_old_root
    from cc_session_tools.cli.migrate_ccsched import DEFAULT_OLD_DIR as ccsched_old_dir
    from cc_session_tools.cli.migrate_sessions_db import DEFAULT_MUTES_FILE, DEFAULT_TAGS_DIR
    from cc_session_tools.cli.migrate_telemetry import DEFAULT_OLD_SOURCE_DIR as telemetry_old_dir
    from cc_session_tools.lib.paths import data_home

    return LegacyMigrationPaths(
        ccmsg_old_root=ccmsg_old_root,
        ccsched_old_dir=ccsched_old_dir,
        tags_dir=DEFAULT_TAGS_DIR,
        mutes_file=DEFAULT_MUTES_FILE,
        telemetry_old_dir=telemetry_old_dir,
        data_home=data_home(),
    )


def _format_digest(unmuted: list[CheckResult]) -> str:
    lines = ["ccst: unmigrated legacy data store(s) detected —"]
    for r in unmuted:
        lines.append(f"  [{r.status.value}] {r.name}: {r.reason}")
    lines.append("")
    lines.append(
        "Run `ccst migrate all` from a plain terminal (NOT inside this Claude Code "
        "session) to migrate. To defer, run: ccst doctor --mute <name>"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        _log_failure("bad-stdin")
        _emit("", "SessionStart")
        return 0
    event = str(data.get("hook_event_name", "SessionStart")) if isinstance(data, dict) else "SessionStart"

    try:
        results = check_pending_data_store_migration(_default_legacy_paths())
        mutes_path = doctor_mutes.default_mutes_path()
        muted = set(doctor_mutes.load_mutes(mutes_path))
        unmuted = filter_unmuted_issues(results, muted)
        # WARN-only findings (migration already ran, old files just not cleaned
        # up) are not worth nagging every session about — only surface FAILs.
        unmuted = [r for r in unmuted if r.status == Status.FAIL]
    except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        _log_failure(type(exc).__name__)
        _emit("", event)
        return 0

    if not unmuted:
        _emit("", event)
        return 0

    _emit(_format_digest(unmuted), event)
    return 0


if __name__ == "__main__":
    sys.exit(main())
