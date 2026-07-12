"""Single source of truth for the skill-marker directory.

Skill markers live under ``~/.cache/claude/markers/`` (override with the
``CCCS_MARKERS_DIR`` env var, or ``$XDG_CACHE_HOME/claude/markers``) and carry
a short TTL (see :mod:`cccs_hooks.confirm_8digit`). Two hooks need to agree on
where that directory is: the 8-digit gate, which honours a fresh marker as an
exemption, and the marker-allow hook, which auto-approves the ``touch`` that
refreshes one. The path lives here so neither hook hard-codes it independently.
"""
from __future__ import annotations

import os
from pathlib import Path


def markers_dir() -> Path:
    """Return the skill-marker directory.

    Resolution order: the ``CCCS_MARKERS_DIR`` env var if set; otherwise
    ``$XDG_CACHE_HOME/claude/markers``; otherwise ``~/.cache/claude/markers``.
    ``Path.home()`` is evaluated at call time so the default tracks ``$HOME``
    (and is overridable in tests by setting ``HOME``); never a baked-in
    absolute path.
    """
    custom = os.environ.get("CCCS_MARKERS_DIR")
    if custom:
        return Path(custom)
    cache_home = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache_home) / "claude" / "markers"
