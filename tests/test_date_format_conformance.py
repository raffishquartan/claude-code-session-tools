"""Every date format string under src/ is a convention form or a listed exemption.

The convention: filenames and folder names use `yyyy.MM.dd`; everything else is ISO `yyyy-MM-dd`
(`yyyy-MM-dd HH:mm` for a time of day, ISO 8601 with `T` and `Z` for machine timestamps).
A new format string must be added to ALLOWED below with one of the REASONS, or the test fails; an
entry that no longer matches a format string in src/ must be deleted.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"

REASONS = {
    "machine-iso": "stored, compared or exchanged timestamp: ISO 8601 with T and Z",
    "identifier": "session tag or other identifier that contains a date",
    "backup-stamp": "backup, snapshot or migration stamp in a file name",
    "human-iso": "printed for a human in the ISO text form",
    "folder-name": "year or month folder name",
    "time-only": "time of day with no date",
    "input-parse": "accepts a user-supplied value, writes nothing",
    "detection-parse": "recognises a value in data, writes nothing",
}

_MACHINE = "%Y-%m-%dT%H:%M:%SZ"
_HUMAN = "%Y-%m-%d %H:%M"
_BACKUP_UTC = "%Y%m%dT%H%M%SZ"

ALLOWED: dict[tuple[str, str], str] = {
    ("cc_session_tools/cli/ccd.py", "%Y%m%d"): "identifier",
    ("cc_session_tools/cli/ccr.py", _HUMAN): "human-iso",
    ("cc_session_tools/cli/ccs.py", "%Y%m%d"): "identifier",
    ("cc_session_tools/cli/ccs.py", "%Y-%m-%d"): "input-parse",
    ("cc_session_tools/cli/ccs.py", _HUMAN): "human-iso",
    ("cc_session_tools/cli/ccs.py", "%Y-%m-%dT%H:%M"): "input-parse",
    ("cc_session_tools/cli/ccs.py", "%Y-%m-%dT%H:%M:%S"): "input-parse",
    ("cc_session_tools/cli/ccsched.py", _MACHINE): "machine-iso",
    ("cc_session_tools/cli/ccst.py", "%Y%m%d%H%M%S"): "backup-stamp",
    ("cc_session_tools/cli/ccst.py", _BACKUP_UTC): "backup-stamp",
    ("cc_session_tools/cli/ccst.py", _HUMAN): "human-iso",
    ("cc_session_tools/cli/migrate_ccmsg.py", _BACKUP_UTC): "backup-stamp",
    ("cc_session_tools/cli/migrate_ccsched.py", _BACKUP_UTC): "backup-stamp",
    ("cc_session_tools/cli/migrate_ccsched.py", _MACHINE): "machine-iso",
    ("cc_session_tools/cli/migrate_sessions_db.py", _BACKUP_UTC): "backup-stamp",
    ("cc_session_tools/cli/migrate_telemetry.py", _BACKUP_UTC): "backup-stamp",
    ("cc_session_tools/lib/install_sync.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/messaging/repository.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/messaging/retention.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/messaging/service.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/messaging/store.py", _BACKUP_UTC): "backup-stamp",
    ("cc_session_tools/lib/pdata/backup.py", "%Y%m%d-%H%M%S"): "backup-stamp",
    ("cc_session_tools/lib/pdata/dump.py", _HUMAN): "human-iso",
    ("cc_session_tools/lib/pdata/init_service.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/pdata/readiness.py", "%Y%m%d"): "detection-parse",
    ("cc_session_tools/lib/pdata/reorganize.py", "%Y"): "folder-name",
    ("cc_session_tools/lib/pdata/reorganize.py", "%m"): "folder-name",
    ("cc_session_tools/lib/proc_lock.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/scheduler/cursor.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/scheduler/registry.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/scheduler/state.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/scheduler/surface.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/sessions_db.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/telemetry_store.py", _MACHINE): "machine-iso",
    ("cc_session_tools/lib/timefmt.py", "%Y-%m-%d %H:%M UTC"): "human-iso",
    ("cc_session_tools/skills/clean-hook-sessions/scripts/clean-hook-sessions.py",
     "%Y%m%d-%H%M%S"): "backup-stamp",
    ("cc_session_tools/skills/clean-hook-sessions/scripts/clean-hook-sessions.py",
     _HUMAN): "human-iso",
    ("cc_session_tools/skills/move-session/scripts/move_session.py",
     "%Y%m%d-%H%M%S"): "backup-stamp",
    ("cc_session_tools/skills/move-session/scripts/move_session.py",
     "%Y-%m-%dT%H:%M:%S.%f"): "machine-iso",
    ("claude_code_usage/query.py", "%Y"): "human-iso",
    ("claude_code_usage/query.py", "%Y-%m"): "human-iso",
    ("claude_code_usage/query.py", "%Y-%m-%d"): "human-iso",
    ("hooks/bash_security_review.py", "%Y%m%d-%H%M"): "identifier",
    ("hooks/cache.py", _MACHINE): "machine-iso",
    ("hooks/cache.py", f"strftime('{_MACHINE}', datetime('now', ?))"): "machine-iso",
    ("hooks/context_window_warning.py", "%H:%M"): "time-only",
    ("hooks/session_tag.py", "%Y%m%d"): "identifier",
    ("hooks/telemetry_query.py", _MACHINE): "machine-iso",
    ("hooks/telemetry_trim.py", _MACHINE): "machine-iso",
}

# A year, month, hour, minute, second or fraction directive. Bare %d/%s are logging placeholders.
_DIRECTIVE = re.compile(r"%[YymHMSfBb]")
_MAX_FORMAT_LENGTH = 80


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def _format_strings() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        if "tests" in path.relative_to(SRC).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
                and len(node.value) < _MAX_FORMAT_LENGTH
                and _DIRECTIVE.search(node.value)
            ):
                found.add((path.relative_to(SRC).as_posix(), node.value))
    return found


def test_every_allowlist_entry_has_a_known_reason() -> None:
    assert set(ALLOWED.values()) <= set(REASONS)


def test_no_date_format_string_outside_the_allowlist() -> None:
    unlisted = sorted(_format_strings() - set(ALLOWED))
    assert not unlisted, (
        "date format string(s) not in the convention: use yyyy-MM-dd / yyyy-MM-dd HH:mm for "
        "output and ISO 8601 with T and Z for stored timestamps, or add an ALLOWED entry with a "
        f"reason from REASONS if it is exempt: {unlisted}"
    )


def test_no_stale_allowlist_entry() -> None:
    stale = sorted(set(ALLOWED) - _format_strings())
    assert not stale, f"ALLOWED entries that match no format string in src/: {stale}"
