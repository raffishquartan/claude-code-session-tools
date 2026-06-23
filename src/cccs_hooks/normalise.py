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
_DATE_YMD_RE = re.compile(r'^\d{8}$')
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
# Public API
# ---------------------------------------------------------------------------

def _classify_token(token: str) -> str:
    """Return the normalised placeholder for *token*, or *token* verbatim.

    Tokens starting with '-' are always returned verbatim (flag passthrough;
    covers negative numbers like -7 as well as flags like --verbose).

    For all other tokens the classification is tested in priority order:
    UUID → ISO date → compact date → number → URL → glob → verbatim.
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

    # No rule matched (git, find, pkg mgr rules come in Task 2)
    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise_read_only(verb: str, args: list[str]) -> str:
    """Apply the read-only builtins normalisation rule.

    Flags are kept verbatim. Numbers become <NUM>. All other non-flag tokens
    collapse to <PATHS>; adjacent <PATHS> entries are deduplicated.
    """
    parts: list[str] = [verb]

    for token in args:
        if token.startswith('-'):
            # Flag — keep verbatim
            parts.append(token)
        elif _NUM_RE.match(token):
            # Numeric argument (e.g. line count)
            parts.append('<NUM>')
        else:
            # Path / filename — collapse, deduplicating adjacent placeholders
            if parts and parts[-1] == '<PATHS>':
                pass  # deduplicate
            else:
                parts.append('<PATHS>')

    return ' '.join(parts)
