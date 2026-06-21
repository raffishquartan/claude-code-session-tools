# src/cc_session_tools/lib/claude_md_install.py
"""Manage a sentinel-delimited block of proactive inter-session-messaging
instructions in the global ~/.claude/CLAUDE.md.

Mirrors shell_install.py: idempotent in-place replace between HTML-comment
markers, dry-run by default, atomic write on apply. Unlike shell_install (which
skips missing rc files), this creates a missing CLAUDE.md so first-time install
works."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from cc_session_tools.lib.messaging.message import write_text_atomic

_SENTINEL_START = "<!-- CCST:messaging START -->"
_SENTINEL_END = "<!-- CCST:messaging END -->"

_BLOCK = f"""\
{_SENTINEL_START}
## Inter-session messaging

You can leave a durable message for another Claude Code session (a specific
session, a whole project, or "whoever is working on X"). Use this proactively
when you discover something relevant to another project, hand off a sub-task, or
need to coordinate with another session - do not wait to be asked.

- When a cross-session message is warranted, use the `send-session-message` skill.
  It helps you choose the recipient (session / project / description), confirm an
  ambiguous recipient with the user, and call `ccmsg send`.
- Delivered messages arrive automatically as injected context. Read a body with
  `ccmsg read <id>`. For a description-addressed proposal, confirm with the user,
  then `ccmsg claim <id>` (first claim wins).
{_SENTINEL_END}
"""


class MarkdownAction(str, Enum):
    ADDED = "added"
    REPLACED = "replaced"
    REMOVED = "removed"
    ALREADY_PRESENT = "already-present"
    NOT_PRESENT = "not-present"


@dataclass(frozen=True)
class MarkdownResult:
    path: Path
    action: MarkdownAction
    message: str


class MalformedBlockError(ValueError):
    """The CCST:messaging sentinel markers in the file are unbalanced,
    duplicated, or out of order, so editing in place could corrupt user prose."""


def _find_block(lines: list[str]) -> tuple[int, int] | None:
    """Return the (start, end) line indices of the first complete
    START..END managed block, or ``None`` if no such pair exists."""
    start = None
    for i, line in enumerate(lines):
        stripped = line.rstrip("\n").rstrip()
        if stripped == _SENTINEL_START:
            start = i
        elif stripped == _SENTINEL_END and start is not None:
            return (start, i)
    return None


def _validate_sentinels(lines: list[str]) -> None:
    """Guard against a malformed marker state before editing in place.

    A safe file has either no markers at all or exactly one well-ordered
    START..END pair. Anything else (a lone marker, duplicates, or reversed
    order) is rejected so a later replace cannot silently swallow the text
    between an orphaned marker and a real one."""
    starts = sum(1 for ln in lines if ln.rstrip("\n").rstrip() == _SENTINEL_START)
    ends = sum(1 for ln in lines if ln.rstrip("\n").rstrip() == _SENTINEL_END)
    if (starts, ends) == (0, 0):
        return
    if (starts, ends) == (1, 1) and _find_block(lines) is not None:
        return
    raise MalformedBlockError(
        "CLAUDE.md CCST:messaging markers are unbalanced or out of order; "
        "fix or remove them by hand and retry"
    )


def install_claude_md(path: Path, *, apply: bool = False) -> MarkdownResult:
    """Insert or update the managed messaging block in ``path``.

    Inserts the block when absent (creating the file if needed), or replaces it
    in place when present (idempotent — no duplication). Dry-run by default;
    pass ``apply=True`` to write. Raises ``MalformedBlockError`` if the existing
    markers are unbalanced."""
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = content.splitlines(keepends=True)
    _validate_sentinels(lines)
    span = _find_block(lines)

    if span is not None:
        start, end = span
        existing = "".join(lines[start : end + 1])
        if existing.rstrip("\n") == _BLOCK.rstrip("\n"):
            return MarkdownResult(path, MarkdownAction.ALREADY_PRESENT, "block already up to date")
        new_content = "".join(lines[:start] + [_BLOCK] + lines[end + 1 :])
        if apply:
            write_text_atomic(path, new_content)
        return MarkdownResult(
            path, MarkdownAction.REPLACED,
            f"{'replaced' if apply else 'would replace'} existing block",
        )

    sep = "" if content.endswith("\n") or not content else "\n"
    new_content = content + sep + _BLOCK
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, new_content)
    return MarkdownResult(
        path, MarkdownAction.ADDED, f"{'added' if apply else 'would add'} block",
    )


def uninstall_claude_md(path: Path, *, apply: bool = False) -> MarkdownResult:
    """Remove the managed messaging block from ``path``, preserving all other
    text. A no-op (``NOT_PRESENT``) if the file or block is absent. Dry-run by
    default; pass ``apply=True`` to write. Raises ``MalformedBlockError`` if the
    markers are unbalanced.

    Note: like ``shell_install``, removal leaves the separator newline that was
    prepended at install time, so a repeated install/uninstall cycle can leave a
    trailing blank line."""
    if not path.exists():
        return MarkdownResult(path, MarkdownAction.NOT_PRESENT, "file does not exist")
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    _validate_sentinels(lines)
    span = _find_block(lines)
    if span is None:
        return MarkdownResult(path, MarkdownAction.NOT_PRESENT, "block not found")
    start, end = span
    new_content = "".join(lines[:start] + lines[end + 1 :])
    if apply:
        write_text_atomic(path, new_content)
    return MarkdownResult(
        path, MarkdownAction.REMOVED, f"{'removed' if apply else 'would remove'} block",
    )
