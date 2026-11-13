"""Recover the Bash command behind a bash-security-review ``input_hash``.

telemetry.db stores only ``sha256:<hex>`` of each reviewed command, never its
text. The same string is the ``input.command`` of a ``Bash`` tool_use record in
a transcript, so the command can be recovered by hashing each Bash command in
the transcripts of the sessions that fired the hash and keeping exact SHA-256
matches. Nothing is guessed: a hash with no exact match is reported as the
session it fired in (``session``), as ``ambiguous`` for a prefix matching
several hashes, or as ``unknown``.

Transcripts searched for session S, under the projects directory
(``~/.claude/projects`` by default): every ``<key>/S.jsonl`` and every
``<key>/S/subagents/*.jsonl`` (the hook's ``session_id`` for a subagent's
command is the parent session's uuid). With ``scan_all`` every transcript is
searched instead. Each file is read at most once per call to ``recover``.

Backs ``ccst telemetry recover`` and the update-command-cache skill's ``list``
previews. Reads only; writes to no store.
"""
from __future__ import annotations

import enum
import glob
import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib import db, paths, telemetry_store

DEFAULT_MAX_CHARS = 200
MIN_PREFIX_LEN = 8
FULL_HASH_LEN = 64

_SHA_PREFIX = "sha256:"
_HEX_RE = re.compile(r"[0-9a-f]+")
# Claude Code writes transcripts as compact JSON, so every Bash tool_use line
# contains this substring; lines without it are skipped before json.loads.
_BASH_MARKER = b'"name":"Bash"'
_HOOK = "bash-security-review"
_SIMPLE_ESCAPES = {"\t": "\\t", "\r": "\\r", "\n": "\\n"}
# Control characters (C0, DEL, C1) and the line and paragraph separators.
_ESCAPED_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})


class RecoveryStatus(enum.Enum):
    COMMAND = "command"
    SESSION = "session"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class MalformedHashError(ValueError):
    """A hash argument that is not 8-64 hex characters after normalisation."""

    def __init__(self, arg: str) -> None:
        super().__init__(
            f"malformed hash argument {arg!r}: expected {MIN_PREFIX_LEN}-{FULL_HASH_LEN} "
            f"hex characters, optionally prefixed with {_SHA_PREFIX!r}"
        )
        self.arg = arg


@dataclass(frozen=True, slots=True)
class Recovery:
    """The outcome for one normalised hash argument.

    ``full_hash`` and ``command`` are set only for ``COMMAND``; ``location`` is
    ``<path>:<line>`` for ``COMMAND``, the session's transcript path (or empty)
    for ``SESSION``, and empty otherwise.
    """

    arg: str
    status: RecoveryStatus
    full_hash: str
    session_id: str
    location: str
    command: str


@dataclass(frozen=True, slots=True)
class _Match:
    path: Path
    line: int
    command: str


def normalise_hash_arg(raw: str) -> str:
    """Lower-case, strip an optional ``sha256:`` prefix, and require 8-64 hex
    characters. Raises MalformedHashError naming ``raw`` otherwise."""
    value = raw.lower()
    if value.startswith(_SHA_PREFIX):
        value = value[len(_SHA_PREFIX):]
    if not MIN_PREFIX_LEN <= len(value) <= FULL_HASH_LEN or not _HEX_RE.fullmatch(value):
        raise MalformedHashError(raw)
    return value


def normalise_args(raws: Iterable[str]) -> list[str]:
    """Normalise every argument (raising on the first malformed one) and
    collapse duplicates, keeping first-seen order."""
    seen: dict[str, None] = {}
    for raw in raws:
        seen.setdefault(normalise_hash_arg(raw), None)
    return list(seen)


def _escape_char(ch: str) -> str:
    if ch in _SIMPLE_ESCAPES:
        return _SIMPLE_ESCAPES[ch]
    if unicodedata.category(ch) in _ESCAPED_CATEGORIES:
        return f"\\u{ord(ch):04x}"
    return ch


def escape_command(command: str) -> str:
    """One-line form of ``command``: tab, CR, LF as ``\\t``, ``\\r``, ``\\n``;
    every other control character (C0, DEL, C1) and the Unicode line and
    paragraph separators as ``\\uXXXX``; everything else literal."""
    return "".join(_escape_char(ch) for ch in command)


def format_command(command: str, max_chars: int) -> str:
    """Escape ``command`` and, if the escaped text is longer than ``max_chars``
    (0 = no limit), cut it without splitting an escape sequence and append
    ``…[+N chars]``, N being the number of escaped characters cut."""
    tokens = [_escape_char(ch) for ch in command]
    total = sum(len(t) for t in tokens)
    if max_chars == 0 or total <= max_chars:
        return "".join(tokens)
    kept: list[str] = []
    used = 0
    for token in tokens:
        if used + len(token) > max_chars:
            break
        kept.append(token)
        used += len(token)
    return "".join(kept) + f"…[+{total - used} chars]"


def format_line(rec: Recovery, max_chars: int) -> str:
    """The tab-separated output line for one Recovery."""
    command = format_command(rec.command, max_chars) if rec.command else ""
    return "\t".join(
        [rec.arg, rec.status.value, rec.full_hash, rec.session_id, rec.location, command]
    )


def iter_bash_commands(path: Path) -> Iterator[tuple[int, str]]:
    """Yield (1-based line number, command) for every Bash tool_use in one
    transcript file. A line that is not valid UTF-8 or JSON (a transcript still
    being written can end mid-record) or whose command cannot be hashed (a lone
    surrogate) cannot hold a recoverable command and is skipped."""
    with path.open("rb") as fh:
        for lineno, raw in enumerate(fh, start=1):
            if _BASH_MARKER not in raw:
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            for command in _bash_commands_in(record):
                try:
                    command.encode()
                except UnicodeEncodeError:
                    continue
                yield lineno, command


def _bash_commands_in(record: object) -> Iterator[str]:
    if not isinstance(record, dict):
        return
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == "Bash"
            and isinstance(block.get("input"), dict)
            and isinstance(block["input"].get("command"), str)
        ):
            yield block["input"]["command"]


def session_of(path: Path) -> str:
    """The session uuid a transcript belongs to: its stem for a main
    transcript, the parent session directory for a subagent transcript."""
    if path.parent.name == "subagents":
        return path.parent.parent.name
    return path.stem


def session_transcripts(projects_dir: Path, session_id: str) -> list[Path]:
    """Main transcript(s) first, then subagent transcripts, each sorted."""
    pattern = glob.escape(session_id)
    return sorted(projects_dir.glob(f"*/{pattern}.jsonl")) + sorted(
        projects_dir.glob(f"*/{pattern}/subagents/*.jsonl")
    )


def all_transcripts(projects_dir: Path) -> list[Path]:
    return sorted(projects_dir.glob("*/*.jsonl")) + sorted(
        projects_dir.glob("*/*/subagents/*.jsonl")
    )


def _fired_sessions(hooks_dir: Path | None) -> dict[str, dict[str, str]]:
    """{full hash: {session_id: latest ts}} for every bash-security-review fire."""
    db_file = telemetry_store.db_path(hooks_dir)
    if not db_file.is_file():
        return {}
    conn = db.connect(db_file, readonly=True)
    try:
        rows = conn.execute(
            "SELECT input_hash, session_id, MAX(ts) FROM telemetry_events "
            "WHERE hook = ? GROUP BY input_hash, session_id",
            (_HOOK,),
        ).fetchall()
    finally:
        conn.close()
    fired: dict[str, dict[str, str]] = {}
    for input_hash, session_id, ts in rows:
        full = input_hash[len(_SHA_PREFIX):] if input_hash.startswith(_SHA_PREFIX) else input_hash
        fired.setdefault(full, {})[session_id] = ts
    return fired


def _scan(files: Sequence[Path], args: Sequence[str]) -> dict[str, _Match]:
    """Read each file once; return the first occurrence of every command whose
    hash starts with one of ``args``."""
    by_len: dict[int, set[str]] = {}
    for arg in args:
        by_len.setdefault(len(arg), set()).add(arg)
    found: dict[str, _Match] = {}
    for path in files:
        for lineno, command in iter_bash_commands(path):
            digest = hashlib.sha256(command.encode()).hexdigest()
            if digest in found:
                continue
            if any(digest[:n] in prefixes for n, prefixes in by_len.items()):
                found[digest] = _Match(path, lineno, command)
    return found


def recover(
    args: Sequence[str],
    *,
    projects_dir: Path | None = None,
    hooks_dir: Path | None = None,
    scan_all: bool = False,
) -> list[Recovery]:
    """One Recovery per argument, in order. ``args`` must already be
    normalised (see normalise_args)."""
    projects = projects_dir if projects_dir is not None else paths.claude_projects_dir()
    fired = _fired_sessions(hooks_dir)
    fired_matches = {arg: sorted(h for h in fired if h.startswith(arg)) for arg in args}

    if scan_all:
        files = all_transcripts(projects)
    else:
        sessions = sorted(
            {s for hashes in fired_matches.values() for h in hashes for s in fired[h]}
        )
        files = [f for s in sessions for f in session_transcripts(projects, s)]
    found = _scan(files, args)

    results: list[Recovery] = []
    for arg in args:
        candidates = set(fired_matches[arg])
        if scan_all:
            candidates.update(h for h in found if h.startswith(arg))
        if not candidates:
            results.append(Recovery(arg, RecoveryStatus.UNKNOWN, "", "", "", ""))
            continue
        if len(candidates) > 1:
            results.append(Recovery(arg, RecoveryStatus.AMBIGUOUS, "", "", "", ""))
            continue
        [full] = candidates
        match = found.get(full)
        if match is not None:
            results.append(Recovery(
                arg, RecoveryStatus.COMMAND, full, session_of(match.path),
                f"{match.path}:{match.line}", match.command,
            ))
            continue
        session_ts = fired[full]
        latest = max(session_ts, key=lambda s: (session_ts[s], s))
        transcripts = session_transcripts(projects, latest)
        location = str(transcripts[0]) if transcripts else ""
        results.append(Recovery(arg, RecoveryStatus.SESSION, "", latest, location, ""))
    return results
