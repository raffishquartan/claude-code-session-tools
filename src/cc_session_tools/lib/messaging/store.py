# src/cc_session_tools/lib/messaging/store.py
"""Message store layout: root resolution, partition derivation, id and slug
generation, and lazy directory creation.

Store layout (under ``store_root()``)::

    <root>/projects/<name>/{inbox,archive/YYYY-MM}/
    <root>/repos/<name>/{inbox,archive/YYYY-MM}/
    <root>/other-paths/<slug>/{inbox,archive/YYYY-MM}/   # keyed on path slug
    <root>/_global/{inbox,archive/YYYY-MM}/              # description + broadcast
    <root>/.cursors/<session-uuid>.json

Partition strings are POSIX-style relative paths (``"projects/alpha"``) so they
are stable cursor keys and store-portable.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib.roots import (
    RootsConfigError,
    is_strict_root,
    load_session_roots,
    matched_session_root,
)

STORE_ROOT_ENV = "CCST_MESSAGES_ROOT"
GLOBAL_PARTITION = "_global"
CURSORS_DIRNAME = ".cursors"

_SLUG_MAX = 31
_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def store_root() -> Path:
    """Resolve the message-store root. ``CCST_MESSAGES_ROOT`` overrides the
    default ``~/.claude/cc-messages`` (tests redirect via the env var)."""
    raw = os.environ.get(STORE_ROOT_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude" / "cc-messages"


def generate_id() -> str:
    """A lexicographically-sortable id: ``YYYYMMDDTHHMMSSZ-<rand4>`` (UTC)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"


def slug_subject(subject: str) -> str:
    """Kebab-case, lower, bounded slug of a subject for human file scanning."""
    cleaned = _SLUG_NON_ALNUM.sub("-", subject.lower()).strip("-")
    cleaned = cleaned[:_SLUG_MAX].strip("-")
    return cleaned or "untitled"


def other_paths_slug(abspath: Path) -> str:
    """Stable slug for a cwd outside all known roots: 8 hex of the path's
    SHA-1 plus a kebab basename, so two different paths never collide."""
    # SHA-1 here is a non-cryptographic path fingerprint, not a security
    # primitive; usedforsecurity=False keeps bandit/FIPS environments happy.
    digest = hashlib.sha1(str(abspath).encode(), usedforsecurity=False).hexdigest()[:8]
    return f"{digest}-{slug_subject(abspath.name)}"


def partition_for_cwd(cwd: Path) -> str:
    """Map a session's cwd to its store partition.

    A cwd whose parent is the strict (PROJ) root → ``projects/<name>``; whose
    parent is a loose (REPO) root → ``repos/<name>``; anything else →
    ``other-paths/<slug>``. Reuses ``roots.py`` so project detection is not
    reinvented."""
    cwd = cwd.resolve() if cwd.exists() else cwd
    try:
        roots = load_session_roots()
    except RootsConfigError:
        # Treat misconfigured roots as absent so messaging degrades to an
        # other-paths partition rather than crashing the caller.
        roots = []
    matched = matched_session_root(cwd, roots) if roots else None
    if matched is not None:
        prefix = "projects" if is_strict_root(matched) else "repos"
        return f"{prefix}/{cwd.name}"
    return f"other-paths/{other_paths_slug(cwd)}"


def partition_dir(partition: str) -> Path:
    """Absolute directory for a partition string under the store root."""
    return store_root() / partition


def ensure_inbox_dir(partition: str) -> Path:
    inbox = partition_dir(partition) / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def archive_dir(partition: str, when: datetime) -> Path:
    month = when.strftime("%Y-%m")
    d = partition_dir(partition) / "archive" / month
    d.mkdir(parents=True, exist_ok=True)
    return d


def cursors_dir() -> Path:
    d = store_root() / CURSORS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def message_filename(message_id: str, subject: str) -> str:
    """``<sortable-id>__<slug>.md`` — id is the routing key, slug is cosmetic."""
    return f"{message_id}__{slug_subject(subject)}.md"
