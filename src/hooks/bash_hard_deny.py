"""PreToolUse hook: hard-deny gate for all Bash commands.

This is the FIRST line of defence for Bash tool calls. It categorically blocks a
fixed set of dangerous patterns before any other hook or permission rule runs. It
then auto-approves everything else so compound or piped commands that don't match
an individual Bash prefix rule in settings.json still work.

Exit code 0 + JSON permissionDecision "allow" = auto-approve (skips prompt).
Exit code 2 = block the tool call; the stderr message is shown to Claude.

Categorically blocked, in file order (checks run in this exact order):

  1. Destructive file operations (rm/rmdir/unlink/shred and their ``g``-prefixed
     names, ``find -delete``, ``find -exec rm``, ``xargs rm``) in any real command
     position. The command is parsed (hooks.shell_parse), so a delete verb inside a
     quoted argument, an echo/printf body, a commit message, a comment or a data
     heredoc does not count, while one inside ``bash -c``/``sh -c``, ``eval``,
     ``$(...)``, backticks, subshells, pipelines, a ``<<<`` here-string fed to a shell,
     or behind the wrappers listed in ``_WRAPPERS`` (``env``, ``command``, ``nohup``,
     ``timeout``, ...), ``\\rm``, ``/bin/rm`` or ``RM`` (case-insensitive filesystems)
     does. Opaque commands - ones that run code the hook cannot read: a dynamic, glob
     or brace command word, a dynamic ``-c``/``eval`` argument, a shell reading stdin
     or a redirect, ``source``, ``trap``, ``ssh``, ``su``, ``watch``, ``awk``,
     ``make``, arithmetic, a function definition, a data heredoc next to a code
     runner (see ``_OPAQUE_RUNNERS``) - get no scratchpad allowance and are blocked
     whenever their text mentions a delete. Unparseable input is opaque.
     Scratchpad exception: a delete is allowed when every target resolves strictly
     inside the calling session's own Claude Code scratchpad,
     ``realpath(CLAUDE_CODE_TMPDIR or /tmp)/claude-<uid>/<encoded project dir>/<session
     id>/scratchpad``, derived from the payload's ``session_id`` and
     ``transcript_path``. Targets must be literal (no variables, substitutions, brace
     expansion or ``~``), must not escape via ``..`` or symlinks, must have an existing
     parent, and may only be missing for verbs that never follow a final symlink; a
     glob is allowed only below the scratchpad root and without bracket expressions or
     a leading-dot wildcard; the root itself (unless ``find -mindepth 1``), ``xargs``
     deletes, ``rmdir -p``, ``find -L``, ``shred`` of a hard-linked file and any
     command that also runs ln/mv/cp/git or similar are blocked; relative targets
     resolve against the payload ``cwd`` only when the command does not change
     directory. Any exception while analysing a command blocks it (a crashing hook
     would let the command run).
  2. The same destructive ops inside an inline ``python``/``node``/``ipython``/...
     script (os.remove, os.unlink, shutil.rmtree, fs.unlinkSync, fs.rmdirSync), with
     the same scratchpad exception for an absolute string-literal argument, unless the
     script also links, renames, moves, changes directory or runs other programs.
  3. Delete-by-move: a direct ``mv`` of a non-tmp file into a tmp-like location
     (/tmp, /var/tmp, /private/tmp, /private/var/tmp, ~/.Trash, ~/tmp, $HOME
     variants) or into Claude Code's per-uid temp root (where scratchpads live), in
     any parsed command position. Moving a file that is already inside a tmp-like
     location is allowed (not a delete).
  4. Delete-by-move inside an inline ``python``/``node -c/-e`` script
     (shutil.move / os.rename / os.replace / fs.renameSync / .move / .copy2 with a
     tmp-like destination).
  5. A destructive op or delete-by-move in a script FILE passed to an interpreter
     (single level of indirection). ``#``-comment lines are stripped first so the
     hook's own source / documentation cannot self-match, and the python/node
     patterns require a trailing ``(`` so regex-literal declarations don't match.
     There is no scratchpad exception here.
  6. The same, but for a heredoc body fed to an interpreter (also no scratchpad
     exception).

Self-cleanup exemption (applies within items 2, 5, 6 only): a Python call to
``os.remove``, ``os.unlink``, or ``shutil.rmtree`` is NOT flagged if its sole
argument is a bare variable that was assigned, earlier in the same
command/script/heredoc body, directly from a ``tempfile.mkstemp``,
``tempfile.mkdtemp``, or ``tempfile.NamedTemporaryFile`` call - i.e. a script
cleaning up a scratch file/dir it created itself in the same breath. Anything
else - a literal path, an attribute access, a variable from elsewhere, the
pathlib delete-method form, or the Node ``fs.*Sync`` forms - is still flagged;
there is no equivalent exemption for those.

  7. ``gh api ... DELETE``.
  8. ``gh release delete`` / ``gh release rm``.
  9. curl/wget with a destructive HTTP method (-X DELETE/POST/PUT/PATCH,
     --request ...) or curl with an implicit-POST ``--data`` flag.
 10. ``sudo`` (any form, incl. after a pipe or ``&&``).
 11. ``opentabs tool call plugin_mark_reviewed`` (self-approval prevention).
 12. Direct reads of the telemetry log (fires.jsonl* or sqlite3 telemetry.db).
 13. ``git branch`` force-delete: ``-D``, or ``-d``/``--delete`` combined with
     ``-f``/``--force`` (any order, including combined short flags like ``-Df``).
     Plain ``git branch -d`` (merge-checked, refuses on unmerged work) is unaffected.
 14. ``git push`` branch deletion: ``--delete``/``-d``, or the ``:<branch>`` empty-refspec
     form (e.g. ``git push origin :old-branch``).

NOTE: deny rules in settings.json take precedence over this hook's "allow". So
even if this hook approves a command, a matching deny rule will still block it.
Other PreToolUse hooks (e.g. bash-security-review) run after this one.
"""
from __future__ import annotations

import fnmatch
import glob
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cc_session_tools.lib.telemetry_store import db_path as _telemetry_db_path
from hooks.shell_parse import DYNAMIC, ShellParseError, SimpleCommand, Word, split_commands

# ---- Common guidance suffix for deletion-style blocks ----
# Repeated in every deletion / delete-by-move BLOCKED message so Claude sees the
# rule every time it is blocked: compile a list, do not delete, present to the
# user at the end of task for manual deletion - plus where deletes ARE allowed.
_DELETION_LIST_GUIDANCE = (
    "Per CLAUDE.md, do NOT delete files mid-task (including by moving to /tmp, "
    "/var/tmp, ~/.Trash or ~/tmp). Compile the file path(s) into a deletion list "
    "in your working notes and present the full list to the user at the end of "
    "task for them to delete manually."
)
_SCRATCHPAD_PATTERN = (
    "<CLAUDE_CODE_TMPDIR or /tmp>/claude-<uid>/<encoded project dir>/<session id>/scratchpad"
)


def _deletion_guidance(scratchpad: Path | None) -> str:
    where = str(scratchpad) if scratchpad is not None else _SCRATCHPAD_PATTERN
    return (
        f"{_DELETION_LIST_GUIDANCE} Exception: rm/rmdir/unlink/shred, find -delete and "
        "inline python/node deletes are allowed when every target is a literal path "
        f"strictly inside this session's scratchpad ({where}) - no variables, no globs at "
        "or above it, no '..' or symlinks leading out of it, and no ln/mv/cp in the same "
        "command. Script files and heredocs fed to an interpreter may not delete at all."
    )


# ---- Session context from the hook payload ----

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ENCODED_DIR_RE = re.compile(r"^[A-Za-z0-9-]+$")


@dataclass(frozen=True, slots=True)
class HookContext:
    """What the payload tells the hook about the calling session.

    ``scratchpad`` is this session's Claude Code scratchpad (None when the payload
    does not identify the session). ``temp_root`` is Claude Code's per-uid temp root,
    which holds every session's scratchpad. ``cwd`` is the shell's working directory.
    """

    scratchpad: Path | None = None
    temp_root: Path | None = None
    cwd: str | None = None


def _claude_temp_root() -> Path:
    """Claude Code's per-uid temp root: ``realpath(CLAUDE_CODE_TMPDIR or /tmp)/claude-<uid>``."""
    base = os.environ.get("CLAUDE_CODE_TMPDIR") or "/tmp"
    return Path(os.path.realpath(base)) / f"claude-{os.getuid()}"


def derive_scratchpad(session_id: object, transcript_path: object) -> Path | None:
    """The calling session's scratchpad, or None when the payload cannot pin it.

    The project-dir component is the name of the directory holding the transcript,
    which Claude Code names with the same encoder it uses for the scratchpad. No
    component below the temp root may be a symlink, so the directory cannot be
    redirected elsewhere."""
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        return None
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    encoded = Path(transcript_path).parent.name
    if not _ENCODED_DIR_RE.match(encoded):
        return None
    path = _claude_temp_root().parent
    for component in (_claude_temp_root().name, encoded, session_id, "scratchpad"):
        path = path / component
        if path.is_symlink():
            return None
    return path


def context_from_payload(data: dict[str, object]) -> HookContext:
    cwd = data.get("cwd")
    return HookContext(
        scratchpad=derive_scratchpad(data.get("session_id"), data.get("transcript_path")),
        temp_root=_claude_temp_root(),
        cwd=cwd if isinstance(cwd, str) and cwd.startswith("/") else None,
    )


# ---- Helpers: tmp-like path detection ----


def _strip_one_quote_layer(p: str) -> str:
    """Strip one leading/trailing ``"`` then one leading/trailing ``'`` — mirrors
    the bash ``${_p#\\"}; ${_p%\\"}; ${_p#\\'}; ${_p%\\'}`` sequence."""
    if p.startswith('"'):
        p = p[1:]
    if p.endswith('"'):
        p = p[:-1]
    if p.startswith("'"):
        p = p[1:]
    if p.endswith("'"):
        p = p[:-1]
    return p


# Literal tmp-like bases: a path equal to the base, or a child of it, counts.
_TMP_LITERAL_BASES = (
    "/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp",
    "~/.Trash", "~/tmp", "$HOME/.Trash", "$HOME/tmp",
)


def _is_tmp_path(raw: str) -> bool:
    """True if *raw* is a path inside a "tmp-like" location where moving a file is
    effectively a delete: /tmp, /var/tmp, ~/.Trash, ~/tmp, or the $HOME-expanded
    variants."""
    p = _strip_one_quote_layer(raw)
    for base in _TMP_LITERAL_BASES:
        if p == base or p.startswith(base + "/"):
            return True
    home = os.environ.get("HOME")
    if home:
        for sub in ("/.Trash", "/tmp"):
            base = home + sub
            if p == base or p.startswith(base + "/"):
                return True
    return False


# Patterns for "contains a tmp-like path anywhere". Used on argument blobs from
# move calls where the destination may be a string literal preceded by text we
# did not capture (variables, function calls).
_CONTAINS_TMP_PATTERNS = (
    re.compile(r"/tmp([/\"' )]|$)"),
    re.compile(r"/var/tmp([/\"' )]|$)"),
    re.compile(r"~/\.Trash"),
    re.compile(r"~/tmp"),
    re.compile(r"\$HOME/\.Trash"),
    re.compile(r"\$HOME/tmp"),
)


def _contains_tmp_path(s: str) -> bool:
    return any(pat.search(s) for pat in _CONTAINS_TMP_PATTERNS)


# ---- Helpers: delete-by-move detection ----

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _mv_segment_violates(seg: str) -> bool:
    """Given a single shell-statement segment (no ; && || | inside), scan the
    tokens for an ``mv`` invocation. Return True if an mv has a tmp-like
    destination AND any source outside a tmp-like location."""
    tokens = seg.split()
    i = 0
    # Skip leading VAR=value env-assignments (e.g. `TMPDIR=/x mv ...`).
    while i < len(tokens) and _ENV_ASSIGN_RE.match(tokens[i]):
        i += 1
    # Only treat mv as a candidate if it is the command token for this segment.
    # This avoids false positives when `mv` appears inside a quoted string
    # (e.g. `echo "remember to mv foo to /tmp"`).
    if i >= len(tokens) or tokens[i] != "mv":
        return False
    split = _mv_split(tokens[i + 1:])
    if split is None:
        return False
    dest, sources = split
    if _is_tmp_path(dest):
        for src in sources:
            if src and not _is_tmp_path(src):
                return True
    return False


def _mv_split(args: list[str]) -> tuple[str, list[str]] | None:
    """Split the arguments of an ``mv`` into (destination, sources), or None when
    there is no destination. ``-t``/``--target-directory`` name the destination."""
    operands: list[str] = []
    dest_via_t = ""
    j = 0
    while j < len(args):
        t = args[j]
        if t in ("-t", "--target-directory"):
            j += 1
            if j < len(args):
                dest_via_t = args[j]
        elif t.startswith("--target-directory="):
            dest_via_t = t[len("--target-directory="):]
        elif t in ("-S", "--suffix"):
            j += 1
        elif not t.startswith("-"):
            operands.append(t)
        j += 1
    if dest_via_t:
        return dest_via_t, operands
    if len(operands) >= 2:
        return operands[-1], operands[:-1]
    return None


# Split on shell statement separators: && || ; | (in that precedence, but a
# single re.split on the alternation is equivalent — each separator is a split).
_STATEMENT_SEP_RE = re.compile(r"&&|\|\||;|\|")


def _detect_mv_to_tmp(cmd: str) -> bool:
    """Split *cmd* on shell statement separators and run _mv_segment_violates on
    each segment. Return True if any segment violates."""
    for seg in _STATEMENT_SEP_RE.split(cmd):
        if not seg.strip():
            continue
        if _mv_segment_violates(seg):
            return True
    return False


# ---- Helpers: git branch/push destructive-delete detection ----


def _branch_segment_force_deletes(seg: str) -> bool:
    """True if *seg* is a `git branch` invocation that force-deletes: `-D`, or `-d`/
    `--delete` combined with `-f`/`--force` in any order (including combined short
    flags like `-Df`/`-fd`). Plain `-d` alone (merge-checked) does not count, and
    neither does bare `-f`/`--force` (a real, non-deleting git-branch flag on its
    own — it force-resets a branch to a new start point)."""
    tokens = seg.split()
    i = 0
    while i < len(tokens) and _ENV_ASSIGN_RE.match(tokens[i]):
        i += 1
    # `git` must be the segment's own command, not e.g. inside a quoted string
    # (mirrors the same discipline `_mv_segment_violates` uses for `mv`).
    if i >= len(tokens) or tokens[i] != "git":
        return False
    try:
        branch_idx = tokens.index("branch", i + 1)
    except ValueError:
        return False
    has_delete = False
    has_force = False
    for t in tokens[branch_idx + 1:]:
        if t == "--delete":
            has_delete = True
        elif t == "--force":
            has_force = True
        elif t.startswith("-") and not t.startswith("--"):
            letters = t[1:]
            if "D" in letters:
                return True
            if "d" in letters:
                has_delete = True
            if "f" in letters:
                has_force = True
    return has_delete and has_force


def _push_segment_deletes_a_branch(seg: str) -> bool:
    """True if *seg* is a `git push` invocation that deletes a remote branch: an
    explicit `--delete`/`-d` flag, or the `:<branch>` empty-refspec form
    (e.g. `git push origin :old-branch`)."""
    tokens = seg.split()
    i = 0
    while i < len(tokens) and _ENV_ASSIGN_RE.match(tokens[i]):
        i += 1
    if i >= len(tokens) or tokens[i] != "git":
        return False
    try:
        push_idx = tokens.index("push", i + 1)
    except ValueError:
        return False
    for t in tokens[push_idx + 1:]:
        if t in ("--delete", "-d"):
            return True
        if t.startswith(":") and len(t) > 1:
            return True
    return False


def _detect_git_branch_force_delete(cmd: str) -> bool:
    return any(_branch_segment_force_deletes(seg) for seg in _STATEMENT_SEP_RE.split(cmd))


def _detect_git_push_branch_delete(cmd: str) -> bool:
    return any(_push_segment_deletes_a_branch(seg) for seg in _STATEMENT_SEP_RE.split(cmd))


# Leading `\.` covers both qualified (shutil.move, fs.renameSync) and chained
# (`require("fs").renameSync`, `Path(x).rename`) idioms. First arg of the call is
# the source; the remainder (up to the closing paren) is the argument blob.
_SCRIPT_MOVE_RE = re.compile(
    r"\.(?:renameSync|rename|replace|move|copy2)\s*\(\s*"
    r"[\"']([^\"']*)[\"']\s*,([^)]*)\)"
)


def _detect_script_move_to_tmp(content: str) -> bool:
    """Scan a script/content string for Python/Node move-style calls whose
    destination is tmp-like and whose source is outside tmp."""
    for m in _SCRIPT_MOVE_RE.finditer(content):
        src = m.group(1)
        after_src = m.group(2)
        if _contains_tmp_path(after_src) and not _is_tmp_path(src):
            return True
    return False


# ---- Helpers: script-file inspection (single level of indirection) ----

_INTERP_RE = re.compile(r"^(python|python3|node|bash|sh|zsh|perl|ruby|lua|php|Rscript)$")
_INTERP_SEP_RE = re.compile(r"[;&|]")

# Patterns require a trailing whitespace / `(` so they match real call sites, not
# regex-literal declarations — this prevents the hook's own source code from
# self-matching when invoked as `bash <this-file>`.
_FILE_RM_RE = re.compile(r"(^|[;&|]\s*)(rm|rmdir|unlink|shred)\s", re.MULTILINE)
_FILE_PY_DEL_RE = re.compile(r"(os\.remove|shutil\.rmtree|fs\.rmdirSync|\.unlink|\.unlinkSync)\(")
_COMMENT_LINE_RE = re.compile(r"^\s*#")


def _strip_comment_lines(text: str) -> str:
    """Drop lines that (after optional leading whitespace) start with ``#``."""
    return "\n".join(ln for ln in text.splitlines() if not _COMMENT_LINE_RE.match(ln))


# ---- Helpers: temp-file self-cleanup exemption ----
#
# A script that creates its own scratch file/dir via tempfile.mkstemp() /
# mkdtemp() / NamedTemporaryFile() and later removes THAT SAME variable is a
# standard, safe idiom (create-then-clean-up-in-a-finally-block), not a
# destructive operation on user data. The exemption is scoped narrowly to a
# call whose sole argument is a bare identifier bound, earlier in the same
# content, directly from one of those three tempfile-creation calls - a
# literal path, an attribute access, an untracked variable, pathlib's
# ``.unlink()`` method form, and the Node ``fs.*Sync`` forms are unaffected
# and still always flagged.
_TEMPFILE_ASSIGN_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*\s*,\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"tempfile\.(?:mkstemp|mkdtemp|NamedTemporaryFile)\s*\("
)
_PY_TEMP_DEL_CALL_RE = re.compile(
    r"(?:os\.remove|os\.unlink|shutil\.rmtree)\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)"
)


def _self_cleanup_temp_vars(content: str) -> set[str]:
    """Variable names assigned directly from a tempfile-creation call in *content*."""
    return {m.group(1) for m in _TEMPFILE_ASSIGN_RE.finditer(content)}


def _has_unsafe_py_delete(content: str, broad_del_re: "re.Pattern[str]") -> bool:
    """True if *content* contains a destructive-delete call (per *broad_del_re*,
    one of the module's ``_..._DEL_RE``/``_..._PY_DEL_RE`` patterns) that is NOT
    the narrow self-cleanup idiom documented above. *broad_del_re* still does the
    actual detection; this just carves the safe idiom back out of its hits."""
    temp_vars = _self_cleanup_temp_vars(content)
    safe_spans = [
        m.span() for m in _PY_TEMP_DEL_CALL_RE.finditer(content) if m.group(1) in temp_vars
    ]
    for m in broad_del_re.finditer(content):
        pos = m.start()
        if not any(start <= pos < end for start, end in safe_spans):
            return True
    return False


def _check_script_file(command: str, guidance: str) -> str | None:
    """Return a BLOCKED message if *command* invokes an interpreter on a readable
    script file whose (comment-stripped) contents contain a destructive op or a
    delete-by-move. Otherwise None.

    Single level of indirection only — we do not recursively inspect scripts
    invoked by the referenced script. Known residual gaps (accepted): scripts
    piped via stdin (`cat x.py | python3`) and binary executables dropped to disk.
    """
    normalised = _INTERP_SEP_RE.sub(" ", command)
    words = normalised.split()
    for idx, word in enumerate(words):
        if not _INTERP_RE.match(word):
            continue
        j = idx + 1
        while j < len(words):
            nxt = words[j]
            # -c/-e mean an INLINE script (handled by earlier checks); stop
            # scanning this segment.
            if nxt in ("-c", "-e"):
                break
            if nxt.startswith("-"):
                j += 1
                continue
            # First non-flag arg — candidate script file. Strip simple quotes.
            file = _strip_one_quote_layer(nxt)
            p = Path(file)
            if p.is_file() and os.access(file, os.R_OK):
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                stripped = _strip_comment_lines(text)
                if _FILE_RM_RE.search(stripped) or _has_unsafe_py_delete(stripped, _FILE_PY_DEL_RE):
                    return (
                        f"BLOCKED: Script file '{file}' contains a destructive file "
                        f"operation. {guidance}"
                    )
                if _detect_mv_to_tmp(stripped):
                    return (
                        f"BLOCKED: Script file '{file}' contains a delete-by-move "
                        f"(mv into tmp-like location). {guidance}"
                    )
                if _detect_script_move_to_tmp(stripped):
                    return (
                        f"BLOCKED: Script file '{file}' contains a delete-by-move in "
                        f"a python/node call. {guidance}"
                    )
            break
    return None


# ---- Helpers: heredoc-body inspection ----

_HD_INTERP_RE = re.compile(
    r"(^|\s)(python3|python|node|bash|sh|zsh|perl|ruby|lua|php|Rscript)([\s;&|]|$)"
)
_HD_OPENER_RE = re.compile(r"<<(-?)[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?")
# `^[[:space:]]*` covers indented bodies (both `<<-` tab-stripped form and
# ordinary `<<` heredocs indented for readability).
_HD_DEL_RM_RE = re.compile(r"(^\s*|[;&|]\s*)(rm|rmdir|unlink|shred)\s", re.MULTILINE)
_HD_DEL_PY_RE = re.compile(
    r"(os\.remove|os\.unlink|shutil\.rmtree|fs\.rmdirSync|fs\.unlinkSync|\.unlink|\.unlinkSync)\("
)


def _scan_heredoc_body(body: str) -> str | None:
    """Return a hit-kind for the first matching pattern in a heredoc body."""
    if _HD_DEL_RM_RE.search(body) or _has_unsafe_py_delete(body, _HD_DEL_PY_RE):
        return "del"
    if _detect_mv_to_tmp(body):
        return "mv-direct"
    if _detect_script_move_to_tmp(body):
        return "mv-script"
    if _GH_API_DELETE_RE.search(body) or _GH_RELEASE_DELETE_RE.search(body):
        return "gh-delete"
    if _HD_CURL_DELETE_RE.search(body):
        return "curl-delete"
    return None


def _check_heredoc(command: str, guidance: str) -> str | None:
    """Return a BLOCKED message if a heredoc body fed to an interpreter contains a
    destructive op, delete-by-move, gh delete, or curl DELETE. Otherwise None.

    Residual gaps (accepted): line-continuation between interpreter and `<<`,
    here-strings (`<<<"..."`), and pipes into stdin (`cat x | python3`).
    """
    if "<<" not in command:
        return None
    in_hd = False
    delim = ""
    dash = ""
    body_lines: list[str] = []
    for line in command.split("\n"):
        if not in_hd:
            m_open = _HD_OPENER_RE.search(line)
            if _HD_INTERP_RE.search(line) and m_open:
                dash = m_open.group(1)
                delim = m_open.group(2)
                in_hd = True
                body_lines = []
        else:
            term = line
            if dash:
                # `<<-` strips leading tabs from the terminator (per bash spec).
                term = term.lstrip("\t")
            if term == delim:
                body = "".join(ln + "\n" for ln in body_lines)
                kind = _scan_heredoc_body(body)
                if kind is not None:
                    message = _HEREDOC_MESSAGES[kind]
                    return f"{message} {guidance}" if kind in _HEREDOC_DELETION_KINDS else message
                # No hit — reset and keep looking for a further heredoc.
                in_hd = False
                delim = ""
                dash = ""
                body_lines = []
                continue
            # Strip `#`-comment lines so documentation/examples don't trip it.
            if not _COMMENT_LINE_RE.match(line):
                body_lines.append(line)
    return None


_HEREDOC_DELETION_KINDS = frozenset({"del", "mv-direct", "mv-script"})
_HEREDOC_MESSAGES: dict[str, str] = {
    "del": (
        "BLOCKED: Heredoc body fed to an interpreter contains a destructive file "
        "operation."
    ),
    "mv-direct": (
        "BLOCKED: Heredoc body fed to an interpreter contains a delete-by-move "
        "(mv into tmp-like location)."
    ),
    "mv-script": (
        "BLOCKED: Heredoc body fed to an interpreter contains a delete-by-move in "
        "a python/node call."
    ),
    "gh-delete": (
        "BLOCKED: Heredoc body fed to an interpreter contains a gh api DELETE or "
        "gh release delete call. Use a dedicated MCP tool or ask the user for "
        "explicit approval."
    ),
    "curl-delete": (
        "BLOCKED: Heredoc body fed to an interpreter contains a curl/wget DELETE "
        "call. Use a dedicated MCP tool or ask the user for explicit approval."
    ),
}


# ---- Command-position scan (parsed simple commands) ----
#
# The command is split into simple commands by hooks.shell_parse, so a delete verb
# counts only where the shell would run it. Wrappers (env, command, nohup, ...),
# shells given -c, eval, find and xargs are unwrapped to the command they run.

_DELETE_VERBS = frozenset(
    {"rm", "rmdir", "unlink", "shred", "grm", "grmdir", "gunlink", "gshred"}
)
# Verbs that never follow a final symlink, so a target that does not exist yet is
# safe: whatever the same command later creates there, only that name is removed.
_NO_FOLLOW_VERBS = frozenset({"rm", "rmdir", "unlink", "grm", "grmdir", "gunlink"})
# Commands that can create or swap a path for a symlink before a later delete in
# the same command runs; their presence disables the scratchpad allowance.
_LINK_OR_MOVE_COMMANDS = frozenset({
    "ln", "gln", "link", "glink", "mv", "gmv", "cp", "gcp", "rsync", "tar", "gtar",
    "unzip", "ditto", "install", "ginstall", "cpio", "git",
})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash", "fish", "csh", "tcsh"})
# Commands that run code the hook cannot see: a string they execute later or
# elsewhere (trap, ssh, su -c, watch, ...), a file (source, make), or a language
# with its own process/file API (awk system(), perl, ruby). Their presence makes the
# command opaque: see _Scan.opaque.
_OPAQUE_RUNNERS = frozenset({
    "source", ".", "trap", "ssh", "su", "watch", "script", "flock", "parallel",
    "awk", "gawk", "mawk", "nawk", "perl", "ruby", "php", "lua", "tclsh", "expect",
    "make", "gmake", "at", "batch", "tmux", "screen", "osascript", "sudo", "doas",
    "let", "[[", "declare", "typeset",
})
_CWD_CHANGERS = frozenset({"cd", "pushd", "popd"})
_KEYWORDS = frozenset({"!", "{", "}", "if", "then", "else", "elif", "while", "until", "do"})
_HEADER_KEYWORDS = frozenset({"for", "case", "select", "in", "esac", "done", "fi"})
_INTERP_WORD_RE = re.compile(
    r"^(i?python[0-9.]*|micropython|pypy[0-9.]*|node|nodejs|bun|deno)$"
)
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=")

# Wrapper -> options that take a separate argument. Every other option is a flag.
_WRAPPERS: dict[str, frozenset[str]] = {
    "command": frozenset(),
    "builtin": frozenset(),
    "exec": frozenset({"-a"}),
    "nohup": frozenset(),
    "time": frozenset(),
    "nice": frozenset({"-n", "--adjustment"}),
    "gnice": frozenset({"-n", "--adjustment"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "setsid": frozenset(),
    "busybox": frozenset(),
    "chronic": frozenset(),
    "coproc": frozenset(),
    "noglob": frozenset(),
    "nocorrect": frozenset(),
    "caffeinate": frozenset({"-t", "-w"}),
    "ionice": frozenset({"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"}),
    "unbuffer": frozenset(),
}
# Wrappers whose options are followed by one positional argument before the command.
_WRAPPERS_WITH_POSITIONAL: dict[str, frozenset[str]] = {
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "gtimeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "chrt": frozenset(),
}
_ENV_ARG_OPTS = frozenset({"-u", "--unset", "-P"})
_XARGS_ARG_OPTS = frozenset({
    "-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s", "--arg-file", "--delimiter",
    "--max-lines", "--max-args", "--max-procs", "--max-chars", "--process-slot-var",
})
_FIND_ACTIONS_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
# xargs and find -exec run a further command; bound how deeply that may nest.
_MAX_NESTING = 20


@dataclass(frozen=True, slots=True)
class _Delete:
    """One delete: each target must pass the scratchpad check. ``allow_missing``
    says whether a not-yet-existing target is safe for this verb. ``overwrites`` marks
    ``shred``, which destroys file content shared with every hard link. ``root_ok``
    marks ``find -mindepth 1``, whose start path may be the scratchpad itself."""

    targets: tuple[Word, ...]
    allow_missing: bool
    overwrites: bool = False
    root_ok: bool = False


@dataclass(slots=True)
class _Scan:
    """What a command does that matters to the delete checks.

    ``opaque`` means the command runs code the hook cannot read (a dynamic command
    word or ``-c``/``eval`` argument, a shell reading stdin, ``source``, ``trap``,
    ``ssh``, a function definition, a heredoc next to a code runner, ...). An opaque
    command gets no scratchpad allowance and is blocked whenever its text mentions a
    delete, which is what the hook did before it parsed commands."""

    deletes: list[_Delete] = field(default_factory=list)
    unresolvable_delete: bool = False
    command_names: set[str] = field(default_factory=set)
    cwd_changed: bool = False
    inline_scripts: list[str] = field(default_factory=list)
    mv_commands: list[tuple[Word, ...]] = field(default_factory=list)
    opaque: bool = False
    runs_code: bool = False
    data_heredoc: bool = False
    decoded_words: list[str] = field(default_factory=list)


_DELETE_VERB_MENTION_RE = re.compile(
    r"\b(" + "|".join(sorted(_DELETE_VERBS, key=len, reverse=True)) + r")\b", re.IGNORECASE
)
# What an opaque or unparseable command must not mention: a delete verb, a find
# delete action, xargs, or an inline-script delete call.
_OPAQUE_DELETE_MENTION_RE = re.compile(
    _DELETE_VERB_MENTION_RE.pattern
    + r"|-delete\b|-exec(dir)?\b|-ok(dir)?\b|\bxargs\b"
    + r"|os\.remove|os\.unlink|os\.rmdir|shutil\.rmtree|unlinkSync|rmdirSync|rmSync"
    + r"|\.unlink\(|\.rmdir\(|\bunlink\(|\bsystem\(",
    re.IGNORECASE,
)


def _skip_options(words: tuple[Word, ...], i: int, arg_opts: frozenset[str]) -> int:
    """Index of the first word after the options starting at *i*. A grouped short
    option (``-iu NAME``, ``-0I {}``) consumes the next word when its last letter takes
    an argument, and its own remainder when an earlier letter does."""
    letters = {o[1] for o in arg_opts if len(o) == 2 and o[0] == "-"}
    while i < len(words):
        v = words[i].value
        if v == "--":
            return i + 1
        if not v.startswith("-") or v == "-":
            return i
        if v in arg_opts:
            i += 2
            continue
        if not v.startswith("--"):
            cluster = v[1:]
            hit = next((k for k, ch in enumerate(cluster) if ch in letters), None)
            if hit is not None and hit == len(cluster) - 1:
                i += 2
                continue
        i += 1
    return i


def _scan_text(text: str, scan: _Scan, *, from_xargs: bool = False, depth: int = 0) -> None:
    """Parse *text* as a command line (a ``bash -c`` string, ``eval`` args) and scan it.
    Unparseable text makes the command opaque."""
    try:
        commands = split_commands(text)
    except ShellParseError:
        scan.opaque = True
        return
    for cmd in commands:
        _scan_simple(cmd, scan, from_xargs=from_xargs, depth=depth)


def _scan_simple(
    cmd: SimpleCommand, scan: _Scan, *, from_xargs: bool = False, depth: int = 0
) -> None:
    if cmd.defines_function or cmd.arithmetic:
        # A function body can hide a cd or a delete behind a plain name; arithmetic
        # expands variables whose values may hold command substitutions.
        scan.opaque = True
    scan.decoded_words.extend(w.value for w in (*cmd.words, *cmd.herestrings))
    ran_code_before = scan.runs_code
    scan.runs_code = False
    _scan_command(
        cmd.words, scan, from_xargs=from_xargs, herestrings=cmd.herestrings,
        stdin_redirected=cmd.stdin_redirected, has_heredoc=cmd.has_heredoc, depth=depth,
    )
    if cmd.has_heredoc and not scan.runs_code:
        scan.data_heredoc = True  # e.g. `cat > s.sh <<EOF`, which something may then run
    scan.runs_code = scan.runs_code or ran_code_before


def _scan_command(
    words: tuple[Word, ...], scan: _Scan, *, from_xargs: bool = False,
    find_roots: tuple[Word, ...] | None = None, herestrings: tuple[Word, ...] = (),
    stdin_redirected: bool = False, has_heredoc: bool = False, depth: int = 0,
    find_root_ok: bool = False,
) -> None:
    """Record what the simple command *words* does that matters to the delete checks.

    ``from_xargs``: the command gets extra arguments from stdin (``xargs``).
    ``find_roots``: the command runs from ``find -exec``; ``{}`` stands for a match
    under these start paths (which may be the scratchpad itself when
    ``find_root_ok``). ``herestrings``: ``<<<`` text fed to its stdin;
    ``stdin_redirected``: stdin comes from a file or process. Command names are
    compared case-insensitively, because a case-insensitive filesystem runs ``RM`` as
    ``rm``, and a leading ``=`` (zsh's command-path expansion) is ignored."""
    if depth > _MAX_NESTING:
        scan.opaque = True
        return
    for k, w in enumerate(words):
        if _INTERP_WORD_RE.match(os.path.basename(w.value).lower()):
            scan.runs_code = True
            code = (*words[k + 1:], *herestrings)
            if any(x.dynamic for x in code) or stdin_redirected:
                scan.opaque = True
            scan.inline_scripts.append(" ".join(x.value for x in code))
            break
    i = 0
    while i < len(words):
        while i < len(words) and (
            _ASSIGNMENT_RE.match(words[i].value) or words[i].value in _KEYWORDS
        ):
            i += 1
        if i >= len(words):
            return
        head = words[i]
        if head.dynamic or head.has_glob or head.brace or head.tilde:
            # `$C dir`, `rm${IFS}x`, `/bin/r[m]`, `{rm,-rf,x}`: the shell decides
            # the command name at run time.
            scan.opaque = True
            name_pattern = os.path.basename(head.value).replace(DYNAMIC, "*")
            if any(fnmatch.fnmatchcase(verb, name_pattern) for verb in _DELETE_VERBS):
                scan.unresolvable_delete = True
            return
        name = os.path.basename(head.value.lstrip("=")).lower()
        scan.command_names.add(name)
        rest = words[i + 1:]
        if head.value in _HEADER_KEYWORDS:
            return
        if head.value == "function":
            scan.opaque = True
            return
        if "/" in head.value:
            scan.runs_code = True  # a script or program run by path
        if name in _CWD_CHANGERS:
            scan.cwd_changed = True
            return
        if name in _OPAQUE_RUNNERS:
            scan.opaque = True
            return
        if name in _WRAPPERS:
            if name == "command" and any(w.value in ("-v", "-V") for w in rest[:1]):
                return
            i = _skip_options(words, i + 1, _WRAPPERS[name])
            continue
        if name in _WRAPPERS_WITH_POSITIONAL:
            i = _skip_options(words, i + 1, _WRAPPERS_WITH_POSITIONAL[name]) + 1
            continue
        if name == "env":
            i = _scan_env(words, i + 1, scan, depth=depth)
            if i < 0:
                return
            continue
        if name in _SHELLS:
            scan.runs_code = True
            _scan_shell(
                rest, scan, from_xargs=from_xargs, herestrings=herestrings,
                stdin_redirected=stdin_redirected, has_heredoc=has_heredoc, depth=depth,
            )
            return
        if name == "eval":
            if any(w.dynamic for w in rest):
                scan.opaque = True
            _scan_text(
                " ".join(w.value for w in rest), scan, from_xargs=from_xargs, depth=depth + 1
            )
            return
        if name in ("xargs", "gxargs"):
            j = _skip_options(rest, 0, _XARGS_ARG_OPTS)
            if j < len(rest):
                _scan_command(rest[j:], scan, from_xargs=True, depth=depth + 1)
            return
        if name in ("find", "gfind"):
            _scan_find(rest, scan, depth=depth)
            return
        if name in ("mv", "gmv"):
            scan.mv_commands.append(rest)
            return
        if name in _DELETE_VERBS:
            _record_delete(
                name, rest, scan, from_xargs=from_xargs, find_roots=find_roots,
                find_root_ok=find_root_ok,
            )
        return


def _scan_env(words: tuple[Word, ...], i: int, scan: _Scan, *, depth: int) -> int:
    """Skip ``env``'s options and assignments; return the command's index, or -1
    when ``-S`` handed the rest to a recursive scan."""
    letters = {o[1] for o in _ENV_ARG_OPTS if len(o) == 2}
    while i < len(words):
        v = words[i].value
        if v == "--":
            i += 1
            break
        if v in ("-S", "--split-string") or v.startswith(("--split-string=", "-S")):
            if v in ("-S", "--split-string"):
                split, after = (words[i + 1].value if i + 1 < len(words) else ""), i + 2
            elif v.startswith("--split-string="):
                split, after = v.split("=", 1)[1], i + 1
            else:
                split, after = v[2:], i + 1
            _scan_text(
                " ".join([split, *(w.value for w in words[after:])]), scan, depth=depth + 1
            )
            return -1
        if v in ("-C", "--chdir") or v.startswith("--chdir=") or (
            v.startswith("-") and not v.startswith("--") and "C" in v
        ):
            scan.cwd_changed = True
            i += 1 if "=" in v or not v.endswith("C") else 2
            continue
        if v.startswith("-") and v != "-":
            grouped_arg = not v.startswith("--") and v[-1] in letters
            i += 2 if v in _ENV_ARG_OPTS or grouped_arg else 1
            continue
        if _ASSIGNMENT_RE.match(v):
            i += 1
            continue
        break
    return i


def _scan_shell(
    args: tuple[Word, ...], scan: _Scan, *, from_xargs: bool, herestrings: tuple[Word, ...],
    stdin_redirected: bool, has_heredoc: bool, depth: int,
) -> None:
    """``bash``/``sh``/... : scan the ``-c`` string or ``<<<`` here-string. A dynamic
    ``-c`` string, or a shell reading its commands from a pipe, a redirect, ``-`` or
    ``/dev/stdin``, makes the command opaque. A script file operand is checked
    elsewhere; an interpreter heredoc is checked by the heredoc scan."""
    has_c = False
    has_s = False
    i = 0
    while i < len(args):
        v = args[i].value
        if v == "--":
            i += 1
            break
        if v in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            i += 2
            continue
        if v.startswith("--"):
            i += 1
            continue
        if len(v) > 1 and v[0] in "-+":
            has_c = has_c or "c" in v[1:]
            has_s = has_s or "s" in v[1:]
            i += 1
            continue
        break
    if has_c:
        if i < len(args):
            if args[i].dynamic:
                scan.opaque = True
            _scan_text(args[i].value, scan, from_xargs=from_xargs, depth=depth + 1)
        return
    for here in herestrings:
        if here.dynamic:
            scan.opaque = True
        _scan_text(here.value, scan, from_xargs=from_xargs, depth=depth + 1)
    operand = args[i] if i < len(args) else None
    if stdin_redirected or has_s or (
        operand is not None
        and (operand.dynamic or operand.value in ("-", "/dev/stdin")
             or operand.value.startswith("/dev/fd/"))
    ):
        scan.opaque = True
    elif operand is None and not herestrings and not has_heredoc:
        scan.opaque = True


def _scan_find(args: tuple[Word, ...], scan: _Scan, *, depth: int) -> None:
    i = 0
    follows = False
    while i < len(args) and (
        args[i].value in ("-H", "-L", "-P", "-D") or args[i].value.startswith("-O")
    ):
        follows = follows or args[i].value in ("-H", "-L")
        i += 2 if args[i].value == "-D" else 1
    roots: list[Word] = []
    while i < len(args) and not (
        args[i].value.startswith("-") or args[i].value in ("(", ")", "!", ",")
    ):
        roots.append(args[i])
        i += 1
    root_words = tuple(roots) or (_literal_word("."),)
    expression = [w.value for w in args[i:]]
    follows = follows or "-follow" in expression
    mindepth_index = expression.index("-mindepth") if "-mindepth" in expression else -1
    root_ok = 0 <= mindepth_index < len(expression) - 1 and expression[
        mindepth_index + 1
    ].isdigit() and int(expression[mindepth_index + 1]) >= 1
    deletes_before = len(scan.deletes)
    while i < len(args):
        v = args[i].value
        if v == "-files0-from":
            scan.unresolvable_delete = True  # start paths come from a file
        elif v == "-delete":
            scan.deletes.append(_Delete(root_words, allow_missing=False, root_ok=root_ok))
        elif v in _FIND_ACTIONS_EXEC:
            if v in ("-execdir", "-okdir"):
                scan.cwd_changed = True  # the command runs in each match's directory
            j = i + 1
            while j < len(args) and args[j].value not in (";", "+"):
                j += 1
            _scan_command(
                args[i + 1:j], scan, find_roots=root_words, find_root_ok=root_ok,
                depth=depth + 1,
            )
            i = j
        i += 1
    if follows and (len(scan.deletes) > deletes_before):
        scan.unresolvable_delete = True


def _literal_word(text: str) -> Word:
    return Word(text, text, (False,) * len(text), tilde=False, brace=False)


def _record_delete(
    verb: str, args: tuple[Word, ...], scan: _Scan, *, from_xargs: bool,
    find_roots: tuple[Word, ...] | None, find_root_ok: bool,
) -> None:
    # xargs appends targets from stdin; `rmdir -p` also removes every parent it empties.
    if from_xargs or (
        verb in ("rmdir", "grmdir")
        and any(w.value == "--parents" or re.match(r"^-[a-z]*p", w.value) for w in args)
    ):
        scan.unresolvable_delete = True
        return
    arg_opts = frozenset({"-n", "-s", "--iterations", "--size", "--random-source"}) if (
        verb in ("shred", "gshred")
    ) else frozenset()
    targets: list[Word] = []
    options_done = False
    i = 0
    while i < len(args):
        w = args[i]
        if not options_done and w.value == "--":
            options_done = True
        elif not options_done and w.value.startswith("-") and w.value != "-":
            i += 1 if w.value not in arg_opts else 2
            continue
        elif w.value == "{}" and find_roots is not None:
            # A match under the start paths: safe exactly when they are.
            scan.deletes.append(_Delete(find_roots, allow_missing=False, root_ok=find_root_ok))
        else:
            targets.append(w)
        i += 1
    if not targets:
        if find_roots is None:
            scan.unresolvable_delete = True
        return
    scan.deletes.append(_Delete(
        tuple(targets), allow_missing=verb in _NO_FOLLOW_VERBS,
        overwrites=verb in ("shred", "gshred"),
    ))


# ---- Scratchpad target checks ----


def _inside(path: str, root: str, *, or_equal: bool) -> bool:
    return (or_equal and path == root) or path.startswith(root.rstrip("/") + "/")


def _absolute(path: str, cwd: str | None) -> str | None:
    if path.startswith("/"):
        return path
    return os.path.join(cwd, path) if cwd is not None else None


def _link_count(path: str) -> int:
    """Hard-link count of *path*, or 0 when it vanished since it was seen (fail closed)."""
    try:
        return os.stat(path).st_nlink
    except OSError:
        return 0


def _path_in_scratchpad(
    path: str, scratchpad: Path, *, allow_missing: bool, overwrites: bool = False,
    root_ok: bool = False,
) -> bool:
    """True if deleting absolute *path* can only affect something strictly inside
    *scratchpad*: its parent exists inside (or is) the scratchpad, it is not ``.``/``..``,
    an existing target resolves inside, and a missing one is allowed for this verb. When
    *overwrites*, a file with other hard links (which may live outside) is refused. When
    *root_ok* (``find -mindepth 1``), the scratchpad itself is also accepted."""
    root = str(scratchpad)
    if root_ok and os.path.isdir(path) and os.path.realpath(path) == root:
        return True
    stripped = path.rstrip("/")
    trailing_slash = stripped != path
    name = os.path.basename(stripped)
    if name in ("", ".", ".."):
        return False
    parent = os.path.realpath(os.path.dirname(stripped))
    if not os.path.isdir(parent) or not _inside(parent, root, or_equal=True):
        return False
    target = os.path.join(parent, name)
    if os.path.lexists(target):
        resolved = os.path.realpath(target)
        if overwrites and os.path.isfile(resolved) and _link_count(resolved) != 1:
            return False
        return _inside(resolved, root, or_equal=False)
    return allow_missing and not trailing_slash


def _glob_in_scratchpad(
    word: Word, cwd: str | None, scratchpad: Path, *, allow_missing: bool, overwrites: bool
) -> bool:
    """A glob is safe when its static prefix is the scratchpad or inside it, no ``..``
    follows the first wildcard, and every current match passes the path check. Python's
    glob must match exactly what bash would, so bracket expressions (``[[:alpha:]]``,
    ``[^x]``, which Python does not implement) and wildcard components starting with
    ``.`` (which older bash expands to ``.`` and ``..``) are refused."""
    chars = list(zip(word.value, word.glob_mask))
    if not word.value.startswith("/"):
        if cwd is None:
            return False
        chars = [(c, False) for c in cwd.rstrip("/") + "/"] + chars
    components: list[list[tuple[str, bool]]] = [[]]
    for c, magic in chars:
        if c == "/" and not magic:
            components.append([])
        else:
            components[-1].append((c, magic))
    for comp in components:
        if any(m for _c, m in comp) and (
            any(c == "[" and m for c, m in comp) or (comp and comp[0][0] == ".")
        ):
            return False
    first_magic = next(i for i, comp in enumerate(components) if any(m for _c, m in comp))
    static = "/".join("".join(c for c, _m in comp) for comp in components[:first_magic]) or "/"
    if any("".join(c for c, _m in comp) == ".." for comp in components[first_magic:]):
        return False
    if not _inside(os.path.realpath(static), str(scratchpad), or_equal=True):
        return False
    pattern = "/".join(
        "".join(c if m else glob.escape(c) for c, m in comp) for comp in components
    )
    suffix = "/" if word.value.endswith("/") else ""
    return all(
        _path_in_scratchpad(
            match + suffix, scratchpad, allow_missing=allow_missing, overwrites=overwrites
        )
        for match in glob.glob(pattern)
    )


def _target_in_scratchpad(word: Word, delete: _Delete, ctx: HookContext, scan: _Scan) -> bool:
    if ctx.scratchpad is None or word.dynamic or word.tilde or word.brace or not word.value:
        return False
    cwd = None if scan.cwd_changed else ctx.cwd
    if word.has_glob:
        return _glob_in_scratchpad(
            word, cwd, ctx.scratchpad,
            allow_missing=delete.allow_missing, overwrites=delete.overwrites,
        )
    path = _absolute(word.value, cwd)
    return path is not None and _path_in_scratchpad(
        path, ctx.scratchpad, allow_missing=delete.allow_missing,
        overwrites=delete.overwrites, root_ok=delete.root_ok,
    )


# Inline python/node delete calls whose first argument is a plain string literal.
_INLINE_LITERAL_DEL_RE = re.compile(
    r"(?:os\.remove|os\.unlink|shutil\.rmtree|fs\.unlinkSync|fs\.rmdirSync)\(\s*"
    r"(?P<q>[\"'])(?P<path>[^\"'\\\n\x00]*)(?P=q)\s*(?:,[^()]*)?\)",
    re.IGNORECASE,
)
# An inline script that also links, renames or moves paths, or runs other programs,
# could redirect its own delete through a symlink it just made, so it gets no
# scratchpad allowance.
_INLINE_LINK_OR_MOVE_RE = re.compile(
    r"symlink|(?<!un)link(?:Sync)?\(|rename|(?<![A-Za-z_])(?:replace|move)\(|copytree"
    r"|system\(|subprocess|popen|spawn|exec[a-zA-Z]*\(|child_process|chdir"
)


def _inline_script_has_unsafe_delete(script: str, ctx: HookContext, allowance: bool) -> bool:
    """True if *script* deletes anything other than its own tempfile scratch or, when
    the command's *allowance* holds, an absolute literal path inside the scratchpad."""
    temp_vars = _self_cleanup_temp_vars(script)
    safe_spans = [
        m.span() for m in _PY_TEMP_DEL_CALL_RE.finditer(script) if m.group(1) in temp_vars
    ]
    allowance = allowance and not _INLINE_LINK_OR_MOVE_RE.search(script)
    for m in _INLINE_DEL_RE.finditer(script):
        if any(start <= m.start() < end for start, end in safe_spans):
            continue
        literal = _INLINE_LITERAL_DEL_RE.match(script, m.start())
        # Only an absolute literal: the script could chdir before deleting a relative one.
        if allowance and literal is not None and ctx.scratchpad is not None:
            path = literal.group("path")
            if path.startswith("/") and _path_in_scratchpad(
                path, ctx.scratchpad, allow_missing=True
            ):
                continue
        return True
    return False


def _mv_into_temp_root(args: tuple[Word, ...], ctx: HookContext, scan: _Scan) -> bool:
    """A parsed ``mv`` that is a delete-by-move: the plain tmp-like rule, plus moving
    a file from outside Claude Code's temp root into it (where the scratchpad lives).

    ``~`` is expanded. A relative destination whose directory a ``cd`` made unknown
    is assumed to be inside the temp root unless every source is relative too and the
    destination has no ``..``. A dynamic destination keeps the plain rule only. A
    source counts as inside only when it resolves inside; a dynamic one never does."""
    if _mv_segment_violates("mv " + " ".join(w.raw for w in args)):
        return True
    split = _mv_split([w.value for w in args])
    if split is None or ctx.temp_root is None or DYNAMIC in split[0]:
        return False
    dest_path, sources = os.path.expanduser(split[0]), split[1]
    root = str(ctx.temp_root)
    cwd = None if scan.cwd_changed else ctx.cwd
    dest_abs = _absolute(dest_path, cwd)
    if dest_abs is None:
        if not scan.cwd_changed:
            return False  # the payload gave no cwd: keep the plain tmp-like rule
        sources_relative = all(
            not src.startswith(("/", "~")) and DYNAMIC not in src for src in sources
        )
        return not (sources_relative and ".." not in dest_path.split("/"))
    if not _inside(os.path.realpath(dest_abs), root, or_equal=True):
        return False
    for src in sources:
        src_abs = None if DYNAMIC in src else _absolute(os.path.expanduser(src), cwd)
        if src_abs is None or not _inside(os.path.realpath(src_abs), root, or_equal=True):
            return True
    return False


_DELETE_BLOCK = "BLOCKED: Destructive file operation (rm/rmdir/unlink/shred/find -delete)"
_INLINE_DELETE_BLOCK = "BLOCKED: Destructive file operation in inline script"
_MV_BLOCK = "BLOCKED: Delete-by-move detected (mv into tmp-like location)"


def _check_parsed_deletes(command: str, ctx: HookContext) -> str | None:
    """Checks 1-3 (delete, inline delete, delete-by-move) on the parsed command."""
    guidance = _deletion_guidance(ctx.scratchpad)
    scan = _Scan()
    try:
        for cmd in split_commands(command):
            _scan_simple(cmd, scan)
    except (ShellParseError, RecursionError):
        scan = _Scan(opaque=True)
    # A data heredoc next to something that runs code may be a script written and run.
    if scan.data_heredoc and scan.runs_code:
        scan.opaque = True
    # Quote-decoded words too: `$'\trm'` hides the verb from the raw text.
    mention_text = "\n".join([command, *scan.decoded_words])
    if scan.opaque and _OPAQUE_DELETE_MENTION_RE.search(mention_text):
        return (
            f"{_DELETE_BLOCK}: the command runs code this hook cannot read (a variable "
            "or substitution as a command or script, eval, source, a shell reading stdin, "
            f"trap/ssh/su/watch, ...) and mentions a delete. {guidance}"
        )
    allowance = (
        ctx.scratchpad is not None
        and not scan.opaque
        and not (scan.command_names & _LINK_OR_MOVE_COMMANDS)
    )
    if scan.unresolvable_delete or any(
        not allowance or not all(_target_in_scratchpad(t, d, ctx, scan) for t in d.targets)
        for d in scan.deletes
    ):
        return f"{_DELETE_BLOCK} outside this session's scratchpad. {guidance}"
    if any(_inline_script_has_unsafe_delete(s, ctx, allowance) for s in scan.inline_scripts):
        return f"{_INLINE_DELETE_BLOCK}. {guidance}"
    if _detect_mv_to_tmp(command) or any(
        _mv_into_temp_root(args, ctx, scan) for args in scan.mv_commands
    ):
        return f"{_MV_BLOCK}. {guidance}"
    return None


# ---- Top-level command patterns ----
# re.MULTILINE so `^`/`$` are line-oriented, matching the bash source's
# `echo "$COMMAND" | grep` line-by-line processing. Patterns using `.*` remain
# single-line-scoped because `.` never matches `\n` (no re.DOTALL) — again
# matching grep.

_INLINE_INTERP_RE = re.compile(r"(python3?|node)\s.*-[ce]\s", re.MULTILINE)
_INLINE_DEL_RE = re.compile(
    r"(os\.remove|os\.unlink|shutil\.rmtree|fs\.unlinkSync|fs\.rmdirSync)",
    re.IGNORECASE | re.MULTILINE,
)
_GH_API_DELETE_RE = re.compile(
    r"gh\s+api\s.*(-X\s*DELETE|--method\s+DELETE|--method=DELETE)",
    re.IGNORECASE | re.MULTILINE,
)
_GH_RELEASE_DELETE_RE = re.compile(r"gh\s+release\s+(delete|rm)\s", re.MULTILINE)
_CURL_METHOD_RE = re.compile(
    r"(curl|wget).*(-X\s*|--request\s*)(DELETE|POST|PUT|PATCH)",
    re.IGNORECASE | re.MULTILINE,
)
_CURL_DATA_RE = re.compile(
    r"curl\s.*(-d\s|--data\s|--data-raw\s|--data-binary\s|--data-urlencode\s)",
    re.IGNORECASE | re.MULTILINE,
)
_SUDO_RE = re.compile(r"(^|[;&|]\s*)sudo\s", re.MULTILINE)
_OPENTABS_MARK_REVIEWED_RE = re.compile(
    r"(^|[^a-zA-Z0-9_])opentabs\s+tool\s+call\s+plugin_mark_reviewed([^a-zA-Z0-9_]|$)",
    re.MULTILINE,
)
# Heredoc-body curl check only catches DELETE (mirrors the bash heredoc scan,
# which is narrower than the top-level curl check).
_HD_CURL_DELETE_RE = re.compile(
    r"(curl|wget).*(-X\s*(DELETE)|--request\s*(DELETE))", re.IGNORECASE | re.MULTILINE
)

# Direct reads of the telemetry log. Two eras are covered: the retired
# fires.jsonl* flat files (basename-based, catches rotated slots too) and
# the current telemetry.db — both any sqlite3 CLI invocation naming it,
# regardless of the query (a read-only SELECT leaks the same session/hash
# data a schema dump or full-table read would), and any binary-dump tool
# (cat/hexdump/xxd/strings/...) reading the file directly, since the
# session_id/cwd_short/verdict/input_hash columns are stored as cleartext
# and a raw byte dump extracts them just as well as SQL would.
_FIRES_READ_RE = re.compile(r"(cat|head|tail|less|more|hexdump|xxd)\s+.*fires.*\.jsonl")
_TELEMETRY_DB_READ_RE = re.compile(
    r"(sqlite3\s+.*telemetry\.db|"
    r"(cat|head|tail|less|more|hexdump|xxd|strings)\s+.*telemetry\.db)"
)
_FIRES_LOG_PATH = _telemetry_db_path().parent / "fires.jsonl"
_TELEMETRY_DB_PATH = _telemetry_db_path()


_ALLOW_PAYLOAD = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "PreToolUse security hook: no destructive patterns detected",
    }
}


def check_command(command: str, ctx: HookContext | None = None) -> str | None:
    """Return a BLOCKED message if *command* matches a hard-deny pattern, else
    None. *ctx* carries the calling session's scratchpad and cwd; without it no
    delete is allowed anywhere."""
    ctx = ctx if ctx is not None else HookContext()
    guidance = _deletion_guidance(ctx.scratchpad)

    # 1-3. Deletes, inline-script deletes and delete-by-move, in command positions.
    parsed_msg = _check_parsed_deletes(command, ctx)
    if parsed_msg is not None:
        return parsed_msg

    # 4. Delete-by-move in an inline python/node -c/-e script.
    if _INLINE_INTERP_RE.search(command) and _detect_script_move_to_tmp(command):
        return f"BLOCKED: Delete-by-move detected in inline script. {guidance}"

    # 5. Destructive op or delete-by-move in a script file passed to an interpreter.
    script_msg = _check_script_file(command, guidance)
    if script_msg is not None:
        return script_msg

    # 6. Destructive op or delete-by-move in a heredoc body fed to an interpreter.
    heredoc_msg = _check_heredoc(command, guidance)
    if heredoc_msg is not None:
        return heredoc_msg

    # 7. gh api with DELETE method.
    if _GH_API_DELETE_RE.search(command):
        return "BLOCKED: gh api with DELETE method. Use a dedicated MCP tool or ask the user for explicit approval."

    # 8. gh release delete / gh release rm.
    if _GH_RELEASE_DELETE_RE.search(command):
        return "BLOCKED: gh release delete must be done by the user in their own terminal."

    # 9a. curl/wget with a destructive HTTP method.
    if _CURL_METHOD_RE.search(command):
        return (
            "BLOCKED: curl/wget with destructive HTTP method (POST/PUT/DELETE/PATCH). "
            "Use a dedicated MCP tool or ask the user for explicit approval."
        )

    # 9b. curl with implicit POST (--data flags).
    if _CURL_DATA_RE.search(command):
        return (
            "BLOCKED: curl with --data (implicit POST). Use a dedicated MCP tool or "
            "ask the user for explicit approval."
        )

    # 10. sudo.
    if _SUDO_RE.search(command):
        return "BLOCKED: Command contains sudo. Ask the user for explicit approval."

    # 11. opentabs plugin_mark_reviewed (self-approval prevention).
    #
    # This MCP tool marks an OpenTabs plugin as reviewed for code-execution in the
    # user's browser. Only the user may approve a plugin. The model MUST NOT
    # auto-approve, even after a model-driven security review and even when the
    # user has said "approve" in conversation — this hook cannot see the
    # conversation, so the only safe rule is to deny and require the user to run it
    # themselves in a separate terminal where this hook does not apply.
    if _OPENTABS_MARK_REVIEWED_RE.search(command):
        return (
            "BLOCKED: opentabs plugin_mark_reviewed must be run by the user manually, "
            "not by Claude through the harness. Even after a model-side 8-digit gate "
            "has matched, this hook adds belt-and-braces by requiring the human to "
            "execute the command in a separate terminal.\n"
            "\n"
            "USER ACTION: copy-paste the command below into any terminal (it does NOT "
            "need to be inside Claude Code) and run it. Then tell Claude the result.\n"
            "\n"
            f"    {command}\n"
        )

    # 12. Direct reads of the telemetry log (fires.jsonl* or telemetry.db).
    #
    # Historically ~/.cache/claude/logs/fires.jsonl*; now telemetry.db (see
    # cc_session_tools.lib.telemetry_store). Both contain session metadata and
    # command hashes. Direct reads are blocked to prevent prompt-injection from
    # harvesting this data. Skills that legitimately need it — ``update-command-cache``
    # (skills/update-command-cache/scripts/update_command_cache.py) and
    # ``analyse-cc-usage`` — set CCST_FIRES_ACCESS=1 before invoking the read.
    if os.environ.get("CCST_FIRES_ACCESS", "0") != "1":
        if _FIRES_READ_RE.search(command):
            return (
                "BLOCKED: Direct reads of the hook telemetry log (fires.jsonl*) are "
                "blocked to prevent credential/session-data exfiltration via prompt "
                "injection. Use the update-command-cache or analyse-cc-usage skill, "
                f"or set CCST_FIRES_ACCESS=1 in the environment. (Log lives at "
                f"{_FIRES_LOG_PATH}.)"
            )
        if _TELEMETRY_DB_READ_RE.search(command):
            return (
                "BLOCKED: Direct sqlite3 reads of telemetry.db are blocked to prevent "
                "credential/session-data exfiltration via prompt injection. Use "
                "`ccst telemetry query`, the update-command-cache skill, or "
                f"set CCST_FIRES_ACCESS=1 in the environment. (DB lives at "
                f"{_TELEMETRY_DB_PATH}.)"
            )

    # 13. git branch force-delete (-D, or -d/--delete + -f/--force).
    if _detect_git_branch_force_delete(command):
        return (
            "BLOCKED: git branch force-delete (-D, or -d/--delete combined with "
            "-f/--force). If this branch's work already landed (e.g. a squash-merged "
            "PR), tell the user the exact command and branch name and ask them to run "
            "it themselves in their own terminal. Plain 'git branch -d <name>' (refuses "
            "on anything not actually merged) is not blocked."
        )

    # 14. git push branch deletion (--delete/-d, or the :<branch> empty-refspec form).
    if _detect_git_push_branch_delete(command):
        return (
            "BLOCKED: git push branch deletion (--delete/-d, or 'git push <remote> "
            ":<branch>'). Deleting a remote branch affects every other clone and any "
            "CI/PR referencing it - tell the user the exact command and branch name and "
            "ask them to run it themselves in their own terminal."
        )

    return None


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(data, dict):
        return 0
    if str(data.get("tool_name", "")) != "Bash":
        return 0

    tool_input = data.get("tool_input")
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command", ""))
    if not command:
        return 0

    try:
        blocked = check_command(command, context_from_payload(data))
    except Exception as exc:  # noqa: BLE001 - a crashing hook would let the command run
        blocked = (
            f"BLOCKED: bash-hard-deny could not analyse this command "
            f"({type(exc).__name__}: {exc}), so it is blocked rather than allowed unchecked."
        )
    if blocked is not None:
        print(blocked, file=sys.stderr)
        return 2

    # ALLOW: everything else. Return JSON with permissionDecision to skip the
    # permission prompt for compound/piped commands that do not match individual
    # allow rules.
    print(json.dumps(_ALLOW_PAYLOAD))
    return 0


if __name__ == "__main__":
    sys.exit(main())
