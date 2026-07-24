"""Root-B path resolution: ~/.local/share/claude and its per-store filenames.

Root B holds everything this repo's tooling creates that (a) isn't Claude
Code's own native store under ~/.claude, and (b) isn't safe to assume
machine-portable. See data-stores-design-spec.md Sections 1-2 (session
20260712-claude-finalise-common-extra-claude-data-store-requirements) for
the full placement rationale. Never assumed to sync across machines.
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_HOME_ENV = "CCST_DATA_HOME"


def data_home() -> Path:
    """Root B directory. Overridable via CCST_DATA_HOME (tests / non-standard setups)."""
    override = os.environ.get(DATA_HOME_ENV)
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "claude"
