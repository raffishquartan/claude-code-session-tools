"""Weekly signal collector (``ccst self-improvement collect-signals``).

Counts cheap, deterministic evidence that the Claude configuration needs a review, writes a
counts-only snapshot to ``signals.env``, returns one summary line, and sends one rate-limited
Telegram message when a health check fails. No language-model call.

Every source is optional and recorded as ``unavailable`` when it cannot be read. A missing
ccmsg or telemetry store is never created. Only counts, ages and fixed text leave this module:
message subjects are never read into anything but a counter.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.self_improvement import state

logger = logging.getLogger(__name__)

LOOP_REVIEW_MAX_DAYS = 21
LOOP_INBOX_MAX_DAYS = 30
AUTO_REVIEW_MAX_DAYS = 8
ESCALATION_INTERVAL_DAYS = 7
MSG_MIN_AGE_DAYS = 14
DENY_WINDOW = dt.timedelta(days=7)
NO_ESCALATE_ENV = "SELF_IMPROVEMENT_NO_ESCALATE"
PERIODIC_REVIEW_COMMAND = "/review-session-and-improve-claude-setup periodic"
RELIABILITY_OPEN_PREFIX = "Open - none fixed"

# Snapshot components, in file order, with the label the summary line and nudge use.
COMPONENTS: tuple[tuple[str, str], ...] = (
    ("INBOX_COUNT", "inbox"),
    ("BACKLOG_OPEN", "entries"),
    ("MSGS_UNCLAIMED_14D", "msgs"),
    ("DENIES_7D", "denies"),
    ("DRIFT_PUSH", "drift"),
    ("RELIABILITY_OPEN", "reliability"),
)

_INBOX_LINE_RE = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})\]")
_OPEN_STATUS_RE = re.compile(r"^- status: (?:open|proposed)\s*$")

Notifier = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class Snapshot:
    collected_at: str
    inbox_count: str
    inbox_oldest_days: str
    backlog_open: str
    msgs_unclaimed_14d: str
    denies_7d: str
    drift_push: str
    reliability_open: str
    signal_total: int
    days_since_review: str
    loop_health: str
    auto_review_health: str

    def to_env(self) -> str:
        rows = (
            ("COLLECTED_AT", self.collected_at),
            ("INBOX_COUNT", self.inbox_count),
            ("INBOX_OLDEST_DAYS", self.inbox_oldest_days),
            ("BACKLOG_OPEN", self.backlog_open),
            ("MSGS_UNCLAIMED_14D", self.msgs_unclaimed_14d),
            ("DENIES_7D", self.denies_7d),
            ("DRIFT_PUSH", self.drift_push),
            ("RELIABILITY_OPEN", self.reliability_open),
            ("SIGNAL_TOTAL", str(self.signal_total)),
            ("DAYS_SINCE_REVIEW", self.days_since_review),
            ("LOOP_HEALTH", self.loop_health),
            ("AUTO_REVIEW_HEALTH", self.auto_review_health),
        )
        return "".join(f"{key}={value}\n" for key, value in rows)

    def summary_line(self) -> str:
        review_text = (
            state.NONE if self.days_since_review == state.NONE else f"{self.days_since_review}d"
        )
        if self.inbox_oldest_days in (state.NONE, state.UNAVAILABLE):
            inbox_text = self.inbox_count
        else:
            inbox_text = f"{self.inbox_count}/{self.inbox_oldest_days}d"
        return (
            f"self-improvement: {self.signal_total} signals (inbox {inbox_text}, entries "
            f"{self.backlog_open}, msgs {self.msgs_unclaimed_14d}, denies {self.denies_7d}, "
            f"drift {self.drift_push}, reliability {self.reliability_open}), last review "
            f"{review_text}, health {self.loop_health}, auto-review {self.auto_review_health}"
        )


# --- sources -----------------------------------------------------------------------------


def _sections(text: str) -> dict[str, list[str]]:
    """Lines under each ``## `` heading, keyed by the full heading line."""
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = out.setdefault(line, [])
        elif current is not None:
            current.append(line)
    return out


def inbox_dates(backlog_text: str) -> list[dt.date]:
    """Valid dates of the dated bullets in the ``## Inbox`` section."""
    dates: list[dt.date] = []
    for line in _sections(backlog_text).get("## Inbox", []):
        match = _INBOX_LINE_RE.match(line)
        if match:
            date = state.parse_date(match.group(1))
            if date is not None:
                dates.append(date)
    return dates


def count_backlog_open(backlog_text: str) -> int:
    """``- status: open|proposed`` lines in ``## Entries``, outside HTML comments."""
    count = 0
    in_comment = False
    for line in _sections(backlog_text).get("## Entries", []):
        if line.startswith("<!--"):
            in_comment = True
        if not in_comment and _OPEN_STATUS_RE.match(line):
            count += 1
        if in_comment and "-->" in line:
            in_comment = False
    return count


def count_unclaimed_messages(now: dt.datetime) -> str:
    """Messages still ``sent`` or ``read`` more than MSG_MIN_AGE_DAYS whole days after
    sending. Counts only: subjects are never read."""
    from cc_session_tools.lib.messaging import service, store

    if not store.db_path().is_file():
        return state.UNAVAILABLE
    try:
        rows = service.list_messages()
    except sqlite3.Error as exc:
        logger.warning("ccmsg store unreadable: %s", type(exc).__name__)
        return state.UNAVAILABLE
    count = 0
    for row in rows:
        if row.status not in ("sent", "read"):
            continue
        try:
            sent = dt.datetime.strptime(row.sent_at, state.ISO_FORMAT).replace(
                tzinfo=dt.timezone.utc
            )
        except ValueError:
            continue
        if (now - sent) // dt.timedelta(days=1) > MSG_MIN_AGE_DAYS:
            count += 1
    return str(count)


def deny_rows(now: dt.datetime) -> list[sqlite3.Row] | None:
    """Telemetry rows with decision ``deny`` in the last 7 days, or None when the telemetry
    store does not exist or cannot be read."""
    from cc_session_tools.lib import telemetry_store
    from hooks.telemetry_query import query_events

    if not telemetry_store.db_path().is_file():
        return None
    since = (now - DENY_WINDOW).strftime(state.ISO_FORMAT)
    try:
        return query_events(decision="deny", since_ts=since, limit=-1)
    except sqlite3.Error as exc:
        logger.warning("telemetry store unreadable: %s", type(exc).__name__)
        return None


def read_drift_count(state_dir: Path) -> str:
    value = state.read_first_line(state_dir / state.DRIFT_COUNT_FILE)
    return value if value is not None and state.is_count(value) else state.UNAVAILABLE


def reliability_files() -> list[Path]:
    """``references/*.md`` of every installed skill, symlinked skill directories included."""
    skills = Path.home() / ".claude" / "skills"
    return sorted(p for p in skills.glob("*/references/*.md") if p.is_file())


def count_reliability_open(files: list[Path]) -> int:
    """Table rows with a cell that starts ``Open - none fixed``."""
    count = 0
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            if not line.startswith("|"):
                continue
            cells = line.split("|")[1:-1]
            if any(cell.lstrip().startswith(RELIABILITY_OPEN_PREFIX) for cell in cells):
                count += 1
    return count


# --- collection --------------------------------------------------------------------------


def collect(now: dt.datetime | None = None) -> Snapshot:
    """Read every source and compute both health results. Reads only."""
    now = now or dt.datetime.now(dt.timezone.utc)
    state_dir = state.state_dir()

    try:
        backlog_text: str | None = state.backlog_path().read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        backlog_text = None
    if backlog_text is None:
        inbox_count = inbox_oldest = backlog_open = state.UNAVAILABLE
    else:
        ages = [state.days_since(d) for d in inbox_dates(backlog_text)]
        inbox_count = str(len(ages))
        inbox_oldest = str(max(ages)) if ages else state.NONE
        backlog_open = str(count_backlog_open(backlog_text))

    rows = deny_rows(now)
    denies = state.UNAVAILABLE if rows is None else str(len(rows))
    msgs = count_unclaimed_messages(now)
    drift = read_drift_count(state_dir)
    reliability = str(count_reliability_open(reliability_files()))

    total = sum(
        int(v) for v in (inbox_count, backlog_open, msgs, denies, drift, reliability)
        if state.is_count(v)
    )

    lines = state.read_log_lines(state_dir)
    last_review = state.last_interactive_review_date(lines)
    days_since_review = state.NONE if last_review is None else str(state.days_since(last_review))

    # A review within 21 days and an Inbox no older than 30 days. An empty Inbox passes the
    # second condition; an unreadable one cannot.
    loop_health = "FAIL"
    if last_review is not None and state.days_since(last_review) <= LOOP_REVIEW_MAX_DAYS:
        if inbox_oldest == state.NONE or (
            state.is_count(inbox_oldest) and int(inbox_oldest) <= LOOP_INBOX_MAX_DAYS
        ):
            loop_health = "PASS"

    last_auto = state.last_auto_date(lines, frozenset({"ran", "skipped"}))
    auto_health = (
        "PASS"
        if last_auto is not None and state.days_since(last_auto) <= AUTO_REVIEW_MAX_DAYS
        else "FAIL"
    )

    return Snapshot(
        collected_at=now.strftime(state.ISO_FORMAT),
        inbox_count=inbox_count,
        inbox_oldest_days=inbox_oldest,
        backlog_open=backlog_open,
        msgs_unclaimed_14d=msgs,
        denies_7d=denies,
        drift_push=drift,
        reliability_open=reliability,
        signal_total=total,
        days_since_review=days_since_review,
        loop_health=loop_health,
        auto_review_health=auto_health,
    )


def escalation_message(snapshot: Snapshot) -> str | None:
    """The Telegram text for a FAIL, or None when both checks pass."""
    failed = []
    if snapshot.loop_health == "FAIL":
        failed.append("loop health FAIL")
    if snapshot.auto_review_health == "FAIL":
        failed.append("auto-review health FAIL")
    if not failed:
        return None
    review_text = (
        state.NONE if snapshot.days_since_review == state.NONE else f"{snapshot.days_since_review}d"
    )
    return (
        f"Self-improvement: {', '.join(failed)} - last review {review_text}, "
        f"{snapshot.signal_total} signals. Run {PERIODIC_REVIEW_COMMAND}"
    )


def escalate(snapshot: Snapshot, notify: Notifier) -> None:
    """At most one message per ESCALATION_INTERVAL_DAYS. The stamp is written only after a
    successful send, so a failed send is retried on the next run."""
    message = escalation_message(snapshot)
    if message is None:
        return
    stamp = state.state_dir() / state.LAST_ESCALATION
    last = state.read_stamp_date(stamp)
    if last is not None and state.days_since(last) < ESCALATION_INTERVAL_DAYS:
        return
    if notify(message):
        try:
            state.atomic_write(stamp, f"{state.today_utc().isoformat()}\n")
        except OSError as exc:
            logger.warning("could not write %s: %s", stamp, exc)


def run(*, escalate_on_fail: bool, notify: Notifier) -> Snapshot:
    """Collect, write ``signals.env`` atomically and escalate. Escalation is also off when
    ``SELF_IMPROVEMENT_NO_ESCALATE=1``: the headless review refreshes the snapshot before
    writing its own auto line, and escalating from there would report a false auto-review
    FAIL and burn the 7-day rate limit."""
    snapshot = collect()
    state_dir = state.state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    state.atomic_write(state_dir / state.SIGNALS_FILE, snapshot.to_env())
    if escalate_on_fail and os.environ.get(NO_ESCALATE_ENV) != "1":
        escalate(snapshot, notify)
    return snapshot
