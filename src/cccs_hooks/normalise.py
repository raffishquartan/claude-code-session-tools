"""Command normalisation for bash-security-review cache.

Produces a human-readable normalised form used as the secondary cache key
pre-image (SHA-256 of the normalised string is the secondary cache key).
Returns None when the command cannot be safely generalised (e.g. interpreter
invocations, relative-path scripts, unknown verbs).

Token classification collapses volatile values (UUIDs, dates, numbers, URLs,
globs) to typed placeholders so structurally identical commands share a cache
entry regardless of the specific values used.

Task 1 scope: _classify_token, read-only builtins (cat/head/tail/wc/ls/stat/
file/basename/dirname/realpath), never-normalise interpreter verbs.
Task 2 will add git, find, and package-manager rules.
"""
from __future__ import annotations

import re
import shlex

# ---------------------------------------------------------------------------
# Module-level compiled regexes
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(r'^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$', re.I)
_DATE_ISO_RE = re.compile(r'^\d{4}[-/]\d{2}[-/]\d{2}$')
_DATE_YMD_RE = re.compile(r'^\d{8}$')  # 8-digit integers in shell args are almost always date-strings (YYYYMMDD)
_NUM_RE = re.compile(r'^-?\d+(\.\d+)?$')
_URL_RE = re.compile(r'^https?://')
_GLOB_RE = re.compile(r'[*?\[]')

# ---------------------------------------------------------------------------
# Never-normalise interpreter verbs
# ---------------------------------------------------------------------------

_NEVER_NORMALISE: frozenset[str] = frozenset({
    'python', 'python3', 'node', 'nodejs', 'ruby', 'perl',
    'bash', 'sh', 'zsh', 'fish',
})

# ---------------------------------------------------------------------------
# Read-only builtins that get path-collapsing treatment
# ---------------------------------------------------------------------------

_READ_ONLY_BUILTINS: frozenset[str] = frozenset({
    'cat', 'head', 'tail', 'wc', 'ls', 'stat', 'file',
    'basename', 'dirname', 'realpath',
})

# ---------------------------------------------------------------------------
# Git rule constants
# ---------------------------------------------------------------------------

_GIT_SAFE_SUBCMDS: frozenset[str] = frozenset({
    'status', 'diff', 'log', 'show', 'fetch', 'pull', 'checkout',
    'merge', 'rebase', 'stash', 'tag', 'describe', 'blame', 'shortlog',
    'cherry-pick', 'branch', 'remote', 'rev-parse', 'ls-files',
    'add', 'commit', 'push',
})
# 'config' is intentionally absent — git config --global can modify system state

_GIT_DANGEROUS_FLAGS: frozenset[str] = frozenset({'--hard', '--force', '-f', '-fd', '--delete'})
_GIT_DANGEROUS_SUBCMDS: frozenset[str] = frozenset({'clean', 'bisect', 'filter-branch', 'gc'})

# ---------------------------------------------------------------------------
# find rule constants
# ---------------------------------------------------------------------------

_FIND_BLOCK_FLAGS: frozenset[str] = frozenset({'-exec', '-execdir', '-delete', '-ok', '-okdir'})
_FIND_SHORT_WORDS: frozenset[str] = frozenset({'f', 'd', 'l', 'b', 'c', 'p', 's'})  # -type/-xtype values

# ---------------------------------------------------------------------------
# Package manager rule constants
# ---------------------------------------------------------------------------

_PKG_SAFE_SUBCMDS: dict[str, frozenset[str]] = {
    'npm':   frozenset({'install', 'ci', 'test', 'build'}),
    # 'run', 'start', 'lint' excluded — script name determines what executes
    'pip':   frozenset({'install', 'show', 'list', 'freeze'}),
    'pip3':  frozenset({'install', 'show', 'list', 'freeze'}),
    'cargo': frozenset({'build', 'test', 'check', 'clippy', 'fmt', 'doc'}),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalise(command: str) -> str | None:
    """Return the normalised form of *command*, or None if it cannot be generalised.

    None is returned when:
    - shlex.split raises ValueError (unparseable shell syntax)
    - The token list is empty
    - The verb is a relative-path invocation (starts with '.')
    - The verb is a never-normalise interpreter
    - No normalisation rule matches the verb (Task 2 will add more)

    For read-only builtins (cat, head, tail, wc, ls, stat, file, basename,
    dirname, realpath):
    - Flag tokens are kept verbatim.
    - Non-flag tokens that classify as <NUM> become <NUM>.
    - All other non-flag tokens collapse to <PATHS>; adjacent <PATHS>
      placeholders are deduplicated.
    """
    try:
        tokens: list[str] = shlex.split(command)
    except ValueError:
        return None

    if not tokens:
        return None

    raw_verb: str = tokens[0]
    verb: str = raw_verb.split('/')[-1]

    # Relative-path script invocations
    if raw_verb.startswith('.'):
        return None

    # Never-normalise interpreters
    if verb in _NEVER_NORMALISE:
        return None

    # Read-only builtins rule
    if verb in _READ_ONLY_BUILTINS:
        return _normalise_read_only(verb, tokens[1:])

    # Git rule
    if verb == 'git' and len(tokens) >= 2:
        subcmd = tokens[1]
        all_flags = set(tokens[2:])
        if subcmd in _GIT_DANGEROUS_SUBCMDS:
            return None
        if all_flags & _GIT_DANGEROUS_FLAGS:
            return None
        if subcmd in _GIT_SAFE_SUBCMDS:
            return f"git {subcmd} <ARGS>"
        return None

    # find rule
    if verb == 'find':
        if set(tokens) & _FIND_BLOCK_FLAGS:
            return None
        parts: list[str] = ['find']
        for t in tokens[1:]:
            if t.startswith('-'):
                parts.append(t)          # verbatim: flag names and negative values like -7
            elif t in _FIND_SHORT_WORDS:
                parts.append(t)          # -type value letters kept verbatim
            else:
                classified = _classify_token(t)
                if classified == t:
                    parts.append('<PATH>')   # unrecognised non-flag token → path
                else:
                    parts.append(classified)  # <GLOB>, <NUM>, <UUID>, etc.
        return ' '.join(parts)

    # Package manager rule
    if verb in _PKG_SAFE_SUBCMDS and len(tokens) >= 2:
        subcmd = tokens[1]
        if subcmd in _PKG_SAFE_SUBCMDS[verb]:
            return f"{verb} {subcmd} <ARGS>"
        return None

    # No rule matched
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_token(token: str) -> str:
    """Return placeholder for *token*, or *token* verbatim.

    Tokens starting with '-' are always returned verbatim (flag passthrough;
    covers negative numbers like -7 as well as flags like --verbose).

    Priority order: UUID → ISO date → compact date → number → URL → glob → verbatim.
    """
    if token.startswith('-'):
        return token

    if _UUID_RE.match(token):
        return '<UUID>'
    if _DATE_ISO_RE.match(token):
        return '<DATE>'
    if _DATE_YMD_RE.match(token):
        return '<DATE>'
    if _NUM_RE.match(token):
        return '<NUM>'
    if _URL_RE.match(token):
        return '<URL>'
    if _GLOB_RE.search(token):
        return '<GLOB>'

    return token


def _normalise_read_only(verb: str, args: list[str]) -> str:
    """Apply the read-only builtins normalisation rule.

    Flags are kept verbatim. Numbers become <NUM>. All other non-flag tokens
    collapse to <PATHS>; adjacent <PATHS> entries are deduplicated.
    """
    parts: list[str] = [verb]

    for token in args:
        if token.startswith('-'):
            parts.append(token)
        else:
            classified = _classify_token(token)
            if classified == '<NUM>':
                parts.append('<NUM>')
            elif parts and parts[-1] == '<PATHS>':
                pass  # deduplicate adjacent <PATHS>
            else:
                parts.append('<PATHS>')

    return ' '.join(parts)
