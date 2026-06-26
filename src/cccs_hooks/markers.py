"""Single source of truth for the skill-marker directory.

Skill markers live under ``~/.claude/hooks/markers/`` and carry a short TTL
(see :mod:`cccs_hooks.confirm_8digit`). Two hooks need to agree on where that
directory is: the 8-digit gate, which honours a fresh marker as an exemption,
and the marker-allow hook, which auto-approves the ``touch`` that refreshes one.
The path lives here so neither hook hard-codes it independently.
"""
from __future__ import annotations

from pathlib import Path


def markers_dir() -> Path:
    """Return the skill-marker directory: ``~/.claude/hooks/markers``.

    Derived from :meth:`Path.home` at call time so it tracks ``$HOME`` (and is
    overridable in tests by setting ``HOME``); never a baked-in absolute path.
    """
    return Path.home() / ".claude" / "hooks" / "markers"
