"""Paths, date helpers and review-log parsing shared by the collector, the nudge and the
headless review.

The state directory and its flat-file formats are a contract shared with other tools (an
interactive review skill appends interactive lines to ``reviews.log``; a config-drift hook
writes ``drift-push-count``), so the formats here must stay byte-compatible:

- ``signals.env``: ``KEY=value`` lines, written by the collector
- ``reviews.log``: one line per review. Interactive lines look like
  ``<ISO time> mode=<B|C> findings=<n> applied=<n> deferred=<n>``; headless lines like
  ``<ISO time> mode=auto status=<ran|skipped|failed|over-budget> sessions=<n> proposals=<n>
  cost_usd=<x|unknown> duration_s=<n>``
- ``drift-push-count``: one non-negative integer, written by another tool
- ``last-nudge``, ``last-escalation``: one ``yyyy-MM-dd`` line
- ``last-auto-review``: an mtime stamp (the start time of the last completed headless run)

Ages in days are UTC calendar days.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import tempfile
from pathlib import Path

from cc_session_tools.lib import paths

REVIEWS_LOG = "reviews.log"
SIGNALS_FILE = "signals.env"
DRIFT_COUNT_FILE = "drift-push-count"
LAST_NUDGE = "last-nudge"
LAST_ESCALATION = "last-escalation"
LAST_AUTO_REVIEW = "last-auto-review"

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

UNAVAILABLE = "unavailable"
NONE = "none"

_INTERACTIVE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T\S* mode=(?:B|C)(?: |$)")
_AUTO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T\S* mode=auto status=(\S+)(?: |$)")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def state_dir() -> Path:
    """``~/.local/share/claude/self-improvement`` (follows ``CCST_DATA_HOME``)."""
    return paths.data_home() / "self-improvement"


def backlog_path() -> Path:
    return Path.home() / ".claude" / "self-improvement-backlog.md"


def today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime(ISO_FORMAT)


def parse_date(text: str) -> dt.date | None:
    """A ``yyyy-MM-dd`` calendar date, or None for anything else (``2026-02-31`` included)."""
    if not _DATE_RE.match(text):
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def days_since(date: dt.date) -> int:
    return (today_utc() - date).days


def is_count(value: str) -> bool:
    """True for a non-negative integer written as digits only."""
    return value.isascii() and value.isdigit()


def read_log_lines(state: Path) -> list[str]:
    try:
        return (state / REVIEWS_LOG).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []


def last_interactive_review_date(lines: list[str]) -> dt.date | None:
    """Date of the newest mode B or C line. Headless ``mode=auto`` lines never count: they
    propose, they do not decide."""
    for line in reversed(lines):
        match = _INTERACTIVE_RE.match(line)
        if match:
            return parse_date(match.group(1))
    return None


def last_interactive_review_line(lines: list[str]) -> str | None:
    for line in reversed(lines):
        if _INTERACTIVE_RE.match(line):
            return line
    return None


def last_auto_line(lines: list[str]) -> str | None:
    for line in reversed(lines):
        if _AUTO_RE.match(line):
            return line
    return None


def last_auto_date(lines: list[str], statuses: frozenset[str]) -> dt.date | None:
    """Date of the newest ``mode=auto`` line whose status is in ``statuses``."""
    for line in reversed(lines):
        match = _AUTO_RE.match(line)
        if match and match.group(2) in statuses:
            return parse_date(match.group(1))
    return None


def log_fields(line: str) -> dict[str, str]:
    """``key=value`` fields of one review-log line (the timestamp is skipped)."""
    fields: dict[str, str] = {}
    for token in line.split()[1:]:
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
    return fields


def last_paid_run_date(lines: list[str]) -> dt.date | None:
    """Date of the newest auto line that may have spent money: ``ran``, ``over-budget``, or
    ``failed`` with a cost other than ``0`` (a timeout or crash logs ``unknown``)."""
    for line in reversed(lines):
        if not _AUTO_RE.match(line):
            continue
        fields = log_fields(line)
        status = fields.get("status")
        if status in ("ran", "over-budget") or (status == "failed" and fields.get("cost_usd") != "0"):
            return parse_date(line[:10])
    return None


def headless_cost_since_last_review(lines: list[str]) -> tuple[int, float, int]:
    """(attempts, known USD, attempts with unknown cost) for the auto lines after the newest
    interactive review."""
    attempts = 0
    known = 0.0
    unknown = 0
    for line in lines:
        if _INTERACTIVE_RE.match(line):
            attempts, known, unknown = 0, 0.0, 0
        elif _AUTO_RE.match(line):
            cost = log_fields(line).get("cost_usd", "")
            attempts += 1
            if cost == "unknown":
                unknown += 1
            else:
                try:
                    known += float(cost)
                except ValueError:
                    unknown += 1
    return attempts, known, unknown


def read_stamp_date(path: Path) -> dt.date | None:
    """The ``yyyy-MM-dd`` on the first line of a stamp file, or None."""
    try:
        first = path.read_text(encoding="utf-8").splitlines()[:1]
    except (FileNotFoundError, UnicodeDecodeError):
        return None
    return parse_date("".join(first[0].split())) if first else None


def read_first_line(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, UnicodeDecodeError):
        return None
    return "".join(lines[0].split()) if lines else ""


def parse_env(text: str) -> dict[str, str]:
    """``KEY=value`` lines; the last occurrence of a key wins. Never executed."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key] = value
    return out


def atomic_write(target: Path, content: str) -> None:
    """Replace ``target`` with ``content`` via a temp file in the same directory and a rename.
    An existing target keeps its permission bits. Raises OSError without touching ``target``
    (and without leaving the temp file) when any step fails."""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        if target.exists():
            shutil.copymode(target, tmp)
        tmp.replace(target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
