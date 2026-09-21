"""Recognise a single simple invocation of a script inside the current project.

Used by bash-security-review's allowlist tier: a script that was reviewed as safe and whose
content hash still matches is allowed regardless of the arguments it is called with. This module
only decides *whether a command is such an invocation and which script it runs*; the store lives
in `hooks.cache` and the tier logic in `hooks.bash_security_review`.

A command matches only if it is one simple command (no pipes, chaining, redirection, command
substitution or variable expansion outside single quotes) of the form `<interpreter> <script> ...`
or a direct path to an executable script, with no interpreter options and no `VAR=value`/`env`
prefix. Anything else falls through to the normal review tiers.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import shlex
from pathlib import Path

MAX_REVIEW_SCRIPT_BYTES = 64 * 1024

# Session scratch scripts are one-offs: never matched, never auto-allowlisted.
_SCRATCH_DIRNAME = "cc-sessions"
_PYTHON_RE = re.compile(r"^python3?(\.\d+)?$")
_SHELL_INTERPRETERS = frozenset({"bash", "sh", "zsh"})
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Unquoted characters that chain, redirect, substitute, expand or background.
_UNQUOTED_FORBIDDEN = frozenset("|;&<>`$()\n\r")


@dataclasses.dataclass(frozen=True, slots=True)
class ScriptRef:
    path: Path
    project_root: Path


@dataclasses.dataclass(frozen=True, slots=True)
class ScriptContent:
    sha256: str
    # None when the script is too large to include in a review prompt.
    text: str | None


def is_simple_command(command: str) -> bool:
    """True if *command* is one simple shell command with no chaining or expansion.

    Quote-aware: `;`, `|`, `>` and so on inside single or double quotes are ordinary argument
    text. Inside double quotes `$` and backticks still expand, so they are rejected there too;
    only single quotes make them literal.
    """
    quote: str | None = None
    i = 0
    while i < len(command):
        c = command[i]
        if quote == "'":
            if c == "'":
                quote = None
        elif quote == '"':
            if c == "\\":
                i += 1
            elif c == '"':
                quote = None
            elif c in "`$":
                return False
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c in _UNQUOTED_FORBIDDEN:
            return False
        i += 1
    return quote is None


def find_project_root(start: Path) -> Path:
    """Nearest ancestor holding a `.git` entry (dir or worktree file), else *start* itself.

    The home directory and filesystem root never count as a project root, so a dotfile repo at
    `$HOME` cannot widen the boundary to every script under it.
    """
    start = start.resolve()
    excluded = {Path.home().resolve(), Path(start.anchor)}
    for candidate in (start, *start.parents):
        if candidate in excluded:
            break
        if (candidate / ".git").exists():
            return candidate
    return start


def _script_token(tokens: list[str]) -> str | None:
    first = tokens[0]
    name = os.path.basename(first)
    if _ASSIGNMENT_RE.match(first):
        return None
    if name == "uv" and first == name:
        if len(tokens) < 3 or tokens[1] != "run":
            return None
        rest = tokens[2:]
        if _PYTHON_RE.match(rest[0]):
            rest = rest[1:]
        return rest[0] if rest else None
    if _PYTHON_RE.match(first) or first in _SHELL_INTERPRETERS or first == "node":
        return tokens[1] if len(tokens) > 1 else None
    if "/" in first:
        return first
    return None


def parse_invocation(command: str, cwd: str) -> ScriptRef | None:
    """Return the project script *command* runs, or None if it is not a matching invocation."""
    if not cwd or not command.strip() or not is_simple_command(command):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None
    script = _script_token(tokens)
    if script is None or script.startswith("-"):
        return None
    candidate = Path(os.path.expanduser(script))
    if not candidate.is_absolute():
        candidate = Path(cwd) / candidate
    real = candidate.resolve()
    if not real.is_file():
        return None
    if tokens[0] == script and not os.access(real, os.X_OK):
        return None  # direct invocation needs an executable file
    root = find_project_root(Path(cwd))
    if not real.is_relative_to(root):
        return None
    if _SCRATCH_DIRNAME in real.relative_to(root).parts:
        return None
    return ScriptRef(path=real, project_root=root)


def read_script(ref: ScriptRef) -> ScriptContent:
    """Read the script once; the hash covers exactly the bytes a review would see."""
    data = ref.path.read_bytes()
    text = data.decode("utf-8", errors="replace") if len(data) <= MAX_REVIEW_SCRIPT_BYTES else None
    return ScriptContent(sha256=hashlib.sha256(data).hexdigest(), text=text)
