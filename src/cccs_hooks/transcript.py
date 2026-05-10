"""Read and walk the parent session's Claude Code transcript JSONL.

The transcript lives at ``~/.claude/projects/<encoded-cwd>/<session_id>.jsonl``
where the encoded cwd replaces every ``/`` in the absolute cwd with ``-``
(so ``/home/chris/repos/foo`` becomes ``-home-chris-repos-foo``).

Each JSONL line is one event. Relevant types:

  * ``user``      - top-level ``timestamp`` (ISO 8601, ``Z`` suffix), and
                    ``message.content`` either a string OR a list of blocks.
                    ``isMeta`` may be present and truthy for synthetic frames.
  * ``assistant`` - ``message.content`` is a list of blocks. Each block has
                    ``type`` of ``text``, ``thinking``, or ``tool_use``.
                    Text blocks have ``text``; tool-use blocks have ``name``
                    and ``input``.
  * ``isSidechain`` - True for subagent traffic. The 8-digit guard runs at
                    the parent boundary, so we keep all turns regardless;
                    the gate semantics only ever look at the parent's most
                    recent user/assistant exchange anyway.
"""
from __future__ import annotations

import dataclasses
import json
import re
import sys
import time
from pathlib import Path

_ASSISTANT_OFFER_PATTERN = re.compile(
    r"respond with[^0-9]*(\d{8})",
    re.IGNORECASE,
)
_FLUSH_RACE_TIMEOUT_S = 1.0
_FLUSH_RACE_INTERVAL_S = 0.05


class TranscriptNotFound(Exception):
    """Raised when the session transcript JSONL cannot be located."""


@dataclasses.dataclass(frozen=True, slots=True)
class Turn:
    role: str
    content: str
    timestamp: str
    tool_name: str = ""


def _encode_cwd(cwd: str) -> str:
    """CC encodes the cwd by swapping ``/`` for ``-``. The leading slash
    becomes a leading dash."""
    return cwd.replace("/", "-")


def _transcript_path(session_id: str, cwd: str) -> Path:
    home = Path.home()
    return home / ".claude" / "projects" / _encode_cwd(cwd) / f"{session_id}.jsonl"


def _extract_text(content: object) -> str:
    """Flatten a message ``content`` value into a single string.

    The user role often supplies a bare string. Both roles can supply a list
    of blocks where each block is either ``{type: text, text: ...}`` or a
    tool-use / thinking block we don't care about for offer-detection.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "text":
                txt = blk.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts)
    return ""


def _parse_turn(record: dict[str, object]) -> Turn | None:
    """Convert a raw JSONL record into a Turn, or None if not a turn we track."""
    rtype = record.get("type")
    if rtype not in ("user", "assistant"):
        return None
    if record.get("isMeta") is True:
        return None
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str):
        return None
    msg = record.get("message")
    if not isinstance(msg, dict):
        return None
    content_field = msg.get("content")
    text = _extract_text(content_field)
    return Turn(
        role=str(rtype),
        content=text,
        timestamp=timestamp,
        tool_name="",
    )


def _read_turns_once(path: Path) -> list[Turn]:
    turns: list[Turn] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(
                    f"[transcript-warn] skipping malformed JSONL line "
                    f"{lineno} of {path}: {e}",
                    file=sys.stderr,
                )
                continue
            if not isinstance(rec, dict):
                continue
            # tool_use blocks live INSIDE assistant messages, but we also
            # surface them as synthetic Turn entries for replay-defence
            # walks (find_intervening_gated_call).
            if rec.get("type") == "assistant" and not rec.get("isMeta"):
                msg = rec.get("message")
                if isinstance(msg, dict):
                    blocks = msg.get("content")
                    if isinstance(blocks, list):
                        ts = rec.get("timestamp", "")
                        if isinstance(ts, str):
                            for blk in blocks:
                                if (
                                    isinstance(blk, dict)
                                    and blk.get("type") == "tool_use"
                                ):
                                    name = blk.get("name")
                                    if isinstance(name, str):
                                        turns.append(
                                            Turn(
                                                role="tool_use",
                                                content="",
                                                timestamp=ts,
                                                tool_name=name,
                                            )
                                        )
            t = _parse_turn(rec)
            if t is not None:
                turns.append(t)
    return turns


def load_transcript(session_id: str, cwd: str) -> list[Turn]:
    """Load and parse the parent-session transcript JSONL into a flat list of
    :class:`Turn` entries (in file order).

    Raises :class:`TranscriptNotFound` when the JSONL file does not exist.

    Implements a small flush-race retry: when the file exists but the most
    recent user turn has not yet been flushed (i.e. there is no ``user`` turn
    in the parsed list, or the file has zero turns), retry every 50 ms for up
    to 1 second before returning the best result.
    """
    path = _transcript_path(session_id, cwd)
    if not path.exists():
        raise TranscriptNotFound(f"transcript not found: {path}")

    deadline = time.monotonic() + _FLUSH_RACE_TIMEOUT_S
    turns = _read_turns_once(path)
    while not any(t.role == "user" for t in turns) and time.monotonic() < deadline:
        time.sleep(_FLUSH_RACE_INTERVAL_S)
        turns = _read_turns_once(path)
    return turns


# ---------- 8-digit code helpers ----------


_EIGHT_DIGITS = re.compile(r"^\d{8}$")


def find_recent_8digit(turns: list[Turn]) -> tuple[str, int] | None:
    """Return ``(code, index)`` if the latest user turn's content is exactly
    eight digits and nothing else (whitespace stripped). Otherwise None."""
    last_user_idx: int | None = None
    for idx in range(len(turns) - 1, -1, -1):
        if turns[idx].role == "user":
            last_user_idx = idx
            break
    if last_user_idx is None:
        return None
    text = turns[last_user_idx].content.strip()
    if _EIGHT_DIGITS.match(text):
        return text, last_user_idx
    return None


def find_preceding_assistant_offer(
    turns: list[Turn], user_idx: int, code: str
) -> bool:
    """Return True iff the assistant turn immediately preceding ``user_idx``
    contains an offer phrase ``Respond with ... <code>`` (case-insensitive,
    any non-digit characters between)."""
    # Walk backwards from user_idx-1 looking for the most recent assistant
    # turn (skipping interleaved tool_use / non-turn entries).
    for idx in range(user_idx - 1, -1, -1):
        t = turns[idx]
        if t.role == "assistant":
            for match in _ASSISTANT_OFFER_PATTERN.finditer(t.content):
                if match.group(1) == code:
                    return True
            return False
        if t.role == "user":
            # Hit an earlier user turn with no assistant in between - no offer.
            return False
    return False


def find_intervening_gated_call(
    turns: list[Turn], user_idx: int, gated_tools: list[str]
) -> bool:
    """Return True iff any ``tool_use`` turn AFTER ``user_idx`` invokes a
    tool in ``gated_tools``. Used as replay-defence so a single 8-digit code
    cannot authorise more than one gated call."""
    gated = set(gated_tools)
    for t in turns[user_idx + 1:]:
        if t.role == "tool_use" and t.tool_name in gated:
            return True
    return False
