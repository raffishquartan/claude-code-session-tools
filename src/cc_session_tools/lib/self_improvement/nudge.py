"""Whether a periodic review is due, and the one-line nudge that says so.

Nothing is ever due until the loop is set up (the backlog has an Inbox, see
``state.loop_set_up``).
Then a review is due when:
- no interactive review is recorded, or the last one is at least 30 days old; or
- the snapshot is valid and its signal total is at least 10; or
- the snapshot is valid, the last review is at least 14 days old and a signal is non-zero.

Reads files only. The message holds only digits and fixed text: a snapshot value that is not
a count (or ``unavailable`` for a component) makes the whole snapshot invalid, so no stray
text from the file can reach the message.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.self_improvement import state
from cc_session_tools.lib.self_improvement.signals import COMPONENTS, review_command

AGE_WITH_SIGNAL_DAYS = 14
AGE_ALONE_DAYS = 30
TOTAL_THRESHOLD = 10
MESSAGE_PREFIX = "[ccst]"
# The config repository's own bash nudge, which shows the same line from the same state.
BASH_NUDGE_SUFFIX = "self-improvement-nudge.sh"


@dataclass(frozen=True, slots=True)
class SnapshotView:
    total: int
    breakdown: str


def read_snapshot(path: Path) -> SnapshotView | None:
    """The total and component breakdown, or None when the file is missing or malformed."""
    text = state.read_text(path)
    if text is None:
        return None
    values = state.parse_env(text)
    total = values.get("SIGNAL_TOTAL", "")
    if not state.is_count(total):
        return None
    parts = []
    for key, label in COMPONENTS:
        value = values.get(key, "")
        if not state.is_count(value) and value != state.UNAVAILABLE:
            return None
        parts.append(f"{label} {value}")
    return SnapshotView(total=int(total), breakdown=", ".join(parts))


def is_due(review_age: int | None, snapshot: SnapshotView | None) -> bool:
    if review_age is None or review_age >= AGE_ALONE_DAYS:
        return True
    if snapshot is None:
        return False
    if snapshot.total >= TOTAL_THRESHOLD:
        return True
    return review_age >= AGE_WITH_SIGNAL_DAYS and snapshot.total >= 1


def message(review_age: int | None, snapshot: SnapshotView | None) -> str:
    review_text = (
        "no review recorded" if review_age is None else f"last review {review_age} days ago"
    )
    signals_text = (
        f"{snapshot.total} signals ({snapshot.breakdown})"
        if snapshot is not None
        else "signals unknown (no valid snapshot - is the self-improvement-signals job registered?)"
    )
    return (
        f"{MESSAGE_PREFIX} Self-improvement review due: {review_text}, {signals_text} - "
        f"run {review_command()}"
    )


def due_message(state_dir: Path) -> str | None:
    """The nudge line when the loop is set up and a review is due, else None. Ignores the
    once-a-day stamp. A last review dated in the future counts as no review recorded."""
    if not state.loop_set_up():
        return None
    last_review = state.last_interactive_review_date(state.read_log_lines(state_dir))
    review_age = None if last_review is None else state.days_since(last_review)
    snapshot = read_snapshot(state_dir / state.SIGNALS_FILE)
    return message(review_age, snapshot) if is_due(review_age, snapshot) else None


def bash_nudge_installed(settings_path: Path) -> bool:
    """True when the settings file registers a SessionStart command ending in the config
    repository's ``self-improvement-nudge.sh``. An unreadable file counts as not installed."""
    try:
        settings = json.loads(state.read_text(settings_path) or "null")
    except json.JSONDecodeError:
        return False
    if not isinstance(settings, dict):
        return False
    hooks = settings.get("hooks")
    groups = hooks.get("SessionStart") if isinstance(hooks, dict) else None
    if not isinstance(groups, list):
        return False
    for group in groups:
        entries = group.get("hooks") if isinstance(group, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            command = entry.get("command") if isinstance(entry, dict) else None
            if isinstance(command, str) and command.strip().strip("'\"").endswith(BASH_NUDGE_SUFFIX):
                return True
    return False


def session_start_message(state_dir: Path, settings_path: Path, today_local: dt.date) -> str | None:
    """The hook's decision: the nudge line, at most once per local calendar day, and never
    when the bash nudge is installed. Records the nudge in ``last-nudge``."""
    if bash_nudge_installed(settings_path):
        return None
    stamp = state_dir / state.LAST_NUDGE
    if state.read_stamp_date(stamp) == today_local:
        return None
    text = due_message(state_dir)
    if text is None:
        return None
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        state.atomic_write(stamp, f"{today_local.isoformat()}\n")
    except OSError:
        # Advisory hook: a stamp that cannot be written only means the nudge may repeat today.
        pass
    return text
