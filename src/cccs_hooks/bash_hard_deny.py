"""PreToolUse hook: hard-deny gate for all Bash commands.

This is the FIRST line of defence for Bash tool calls. It categorically blocks a
fixed set of dangerous patterns before any other hook or permission rule runs. It
then auto-approves everything else so compound or piped commands that don't match
an individual Bash prefix rule in settings.json still work.

Exit code 0 + JSON permissionDecision "allow" = auto-approve (skips prompt).
Exit code 2 = block the tool call; the stderr message is shown to Claude.

Categorically blocked, in file order (checks run in this exact order):

  1. Destructive file operations (rm/rmdir/unlink/shred), anywhere in a compound
     command (at line start or after ``;`` / ``&`` / ``|``).
  2. The same destructive ops inside an inline ``python``/``node -c/-e`` script
     (os.remove, os.unlink, shutil.rmtree, fs.unlinkSync, fs.rmdirSync).
  3. Delete-by-move: a direct ``mv`` of a non-tmp file into a tmp-like location
     (/tmp, /var/tmp, ~/.Trash, ~/tmp, $HOME variants). Moving a file that is
     already inside a tmp-like location is allowed (not a delete).
  4. Delete-by-move inside an inline ``python``/``node -c/-e`` script
     (shutil.move / os.rename / os.replace / fs.renameSync / .move / .copy2 with a
     tmp-like destination).
  5. A destructive op or delete-by-move in a script FILE passed to an interpreter
     (single level of indirection). ``#``-comment lines are stripped first so the
     hook's own source / documentation cannot self-match, and the python/node
     patterns require a trailing ``(`` so regex-literal declarations don't match.
  6. The same, but for a heredoc body fed to an interpreter.
  7. ``gh api ... DELETE``.
  8. ``gh release delete`` / ``gh release rm``.
  9. curl/wget with a destructive HTTP method (-X DELETE/POST/PUT/PATCH,
     --request ...) or curl with an implicit-POST ``--data`` flag.
 10. ``sudo`` (any form, incl. after a pipe or ``&&``).
 11. ``opentabs tool call plugin_mark_reviewed`` (self-approval prevention).
 12. Direct reads of the fires.jsonl telemetry log.

NOTE: deny rules in settings.json take precedence over this hook's "allow". So
even if this hook approves a command, a matching deny rule will still block it.
Other PreToolUse hooks (e.g. bash-security-review) run after this one.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from cccs_hooks.telemetry import _DEFAULT_HOOKS_DIR

# ---- Common guidance suffix for deletion-style blocks ----
# Repeated verbatim in every deletion / delete-by-move BLOCKED message so Claude
# sees the rule every time it is blocked: compile a list, do not delete, present
# to the user at the end of task for manual deletion.
_DELETION_GUIDANCE = (
    "Per CLAUDE.md, do NOT delete files mid-task (including by moving to /tmp, "
    "/var/tmp, ~/.Trash or ~/tmp). Compile the file path(s) into a deletion list "
    "in your working notes and present the full list to the user at the end of "
    "task for them to delete manually."
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
_TMP_LITERAL_BASES = ("/tmp", "/var/tmp", "~/.Trash", "~/tmp", "$HOME/.Trash", "$HOME/tmp")


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
    args: list[str] = []
    dest_via_t = ""
    j = i + 1
    while j < len(tokens):
        t = tokens[j]
        if t in ("-t", "--target-directory"):
            j += 1
            if j < len(tokens):
                dest_via_t = tokens[j]
        elif t.startswith("--target-directory="):
            dest_via_t = t[len("--target-directory="):]
        elif t in ("-S", "--suffix"):
            j += 1
        elif t.startswith("--suffix=") or t.startswith("--backup="):
            pass
        elif t.startswith("-"):
            pass
        else:
            args.append(t)
        j += 1
    if dest_via_t:
        dest = dest_via_t
        sources = args
    elif len(args) >= 2:
        dest = args[-1]
        sources = args[:-1]
    else:
        return False
    if _is_tmp_path(dest):
        for src in sources:
            if src and not _is_tmp_path(src):
                return True
    return False


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


def _check_script_file(command: str) -> str | None:
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
                if _FILE_RM_RE.search(stripped) or _FILE_PY_DEL_RE.search(stripped):
                    return (
                        f"BLOCKED: Script file '{file}' contains a destructive file "
                        f"operation. {_DELETION_GUIDANCE}"
                    )
                if _detect_mv_to_tmp(stripped):
                    return (
                        f"BLOCKED: Script file '{file}' contains a delete-by-move "
                        f"(mv into tmp-like location). {_DELETION_GUIDANCE}"
                    )
                if _detect_script_move_to_tmp(stripped):
                    return (
                        f"BLOCKED: Script file '{file}' contains a delete-by-move in "
                        f"a python/node call. {_DELETION_GUIDANCE}"
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
    if _HD_DEL_RM_RE.search(body) or _HD_DEL_PY_RE.search(body):
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


def _check_heredoc(command: str) -> str | None:
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
                    return _HEREDOC_MESSAGES[kind]
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


_HEREDOC_MESSAGES: dict[str, str] = {
    "del": (
        "BLOCKED: Heredoc body fed to an interpreter contains a destructive file "
        f"operation. {_DELETION_GUIDANCE}"
    ),
    "mv-direct": (
        "BLOCKED: Heredoc body fed to an interpreter contains a delete-by-move "
        f"(mv into tmp-like location). {_DELETION_GUIDANCE}"
    ),
    "mv-script": (
        "BLOCKED: Heredoc body fed to an interpreter contains a delete-by-move in "
        f"a python/node call. {_DELETION_GUIDANCE}"
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


# ---- Top-level command patterns ----
# re.MULTILINE so `^`/`$` are line-oriented, matching the bash source's
# `echo "$COMMAND" | grep` line-by-line processing. Patterns using `.*` remain
# single-line-scoped because `.` never matches `\n` (no re.DOTALL) — again
# matching grep.

_DEL_RM_RE = re.compile(r"(^|[;&|]\s*)(rm|rmdir|unlink|shred)\s", re.MULTILINE)
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

# Direct reads of the fires.jsonl telemetry log. The pattern is basename-based
# (`fires.*\.jsonl`) so it catches the current location, rotated slots, and
# compressed variants alike — the real directory is
# `cccs_hooks.telemetry._DEFAULT_HOOKS_DIR` (~/.cache/claude/logs), NOT the stale
# ~/.claude/hooks path the original bash comment referenced.
_FIRES_READ_RE = re.compile(r"(cat|head|tail|less|more|hexdump|xxd)\s+.*fires.*\.jsonl")
_FIRES_LOG_PATH = _DEFAULT_HOOKS_DIR / "fires.jsonl"


_ALLOW_PAYLOAD = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "PreToolUse security hook: no destructive patterns detected",
    }
}


def check_command(command: str) -> str | None:
    """Return a BLOCKED message if *command* matches a hard-deny pattern, else
    None. Checks run in the same order as the bash source."""
    # 1. Destructive file operations (deletion).
    if _DEL_RM_RE.search(command):
        return (
            "BLOCKED: Destructive file operation (rm/rmdir/unlink/shred). "
            f"{_DELETION_GUIDANCE}"
        )

    # 2. Python/Node destructive file ops in an inline script.
    if _INLINE_INTERP_RE.search(command) and _INLINE_DEL_RE.search(command):
        return f"BLOCKED: Destructive file operation in inline script. {_DELETION_GUIDANCE}"

    # 3. Delete-by-move (direct mv command to a tmp-like location).
    if _detect_mv_to_tmp(command):
        return (
            "BLOCKED: Delete-by-move detected (mv into tmp-like location). "
            f"{_DELETION_GUIDANCE}"
        )

    # 4. Delete-by-move in an inline python/node -c/-e script.
    if _INLINE_INTERP_RE.search(command) and _detect_script_move_to_tmp(command):
        return f"BLOCKED: Delete-by-move detected in inline script. {_DELETION_GUIDANCE}"

    # 5. Destructive op or delete-by-move in a script file passed to an interpreter.
    script_msg = _check_script_file(command)
    if script_msg is not None:
        return script_msg

    # 6. Destructive op or delete-by-move in a heredoc body fed to an interpreter.
    heredoc_msg = _check_heredoc(command)
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

    # 12. Direct reads of the fires.jsonl telemetry log.
    #
    # The telemetry log at ``_DEFAULT_HOOKS_DIR/fires.jsonl*`` (i.e.
    # ~/.cache/claude/logs/fires.jsonl*) contains session metadata and command
    # hashes. Direct reads via cat/tail/head/less/more/hexdump/xxd are blocked to
    # prevent prompt-injection from harvesting this data. Skills that legitimately
    # need the log — ``update-command-cache``
    # (skills/update-command-cache/scripts/update_command_cache.py) and
    # ``analyse-cc-usage`` — set CCCS_FIRES_ACCESS=1 before invoking the read.
    if os.environ.get("CCCS_FIRES_ACCESS", "0") != "1":
        if _FIRES_READ_RE.search(command):
            return (
                "BLOCKED: Direct reads of the hook telemetry log (fires.jsonl*) are "
                "blocked to prevent credential/session-data exfiltration via prompt "
                "injection. Use the update-command-cache or analyse-cc-usage skill, "
                f"or set CCCS_FIRES_ACCESS=1 in the environment. (Log lives at "
                f"{_FIRES_LOG_PATH}.)"
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

    blocked = check_command(command)
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
