# src/cc_session_tools/lib/messaging/store.py
"""Message store layout: root resolution, partition derivation, id and slug
generation.

Store layout (under ``store_root()``)::

    <root>/ccmsg.db

A single WAL-mode SQLite database holds every message row and the per-session
delivery cursors; ``repository.py`` owns all SQL. Partition strings are
POSIX-style relative paths (``"projects/alpha"``) kept as the ``to_location``
routing column and as stable cursor keys.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import paths
from cc_session_tools.lib.roots import (
    RootsConfigError,
    is_strict_root,
    load_session_roots,
    matched_session_root,
    proj_root,
    repo_root,
)

STORE_ROOT_ENV = "CCST_MESSAGES_ROOT"
GLOBAL_PARTITION = "_global"
DB_FILENAME = "ccmsg.db"
# Retained only until Task 8 removes the flat-file helpers below.
CURSORS_DIRNAME = ".cursors"

_SLUG_MAX = 31
_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def store_root() -> Path:
    """Directory holding ``ccmsg.db``. ``CCST_MESSAGES_ROOT`` overrides the
    default ``paths.data_home()`` (tests redirect via the env var)."""
    raw = os.environ.get(STORE_ROOT_ENV)
    if raw:
        return Path(raw).expanduser()
    return paths.data_home()


def db_path() -> Path:
    return store_root() / DB_FILENAME


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


def partition_for_project(name: str) -> str:
    """Partition a project-addressed message routes to.

    A session working in project ``name`` sweeps ``partition_for_cwd`` of its
    cwd, so a project-addressed message must land in that same partition:
    ``projects/<name>`` when ``name`` resolves under the strict (PROJ) root,
    ``repos/<name>`` under the loose (REPO) root. When ``name`` cannot be
    resolved locally, fall back to ``_global`` — every session sweeps it and
    ``addressing.targets`` still matches on the project label, so the message is
    delivered (just less selectively)."""
    pr = proj_root()
    if pr is not None and (pr / name).is_dir():
        return partition_for_cwd(pr / name)
    rr = repo_root()
    if rr is not None and (rr / name).is_dir():
        return partition_for_cwd(rr / name)
    return GLOBAL_PARTITION


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
