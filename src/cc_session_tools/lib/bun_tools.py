"""Locate tools installed through bun (bun itself, ccusage).

`bun add -g` installs into ``$BUN_INSTALL/bin`` (default ``~/.bun/bin``). That directory is
only on ``PATH`` if the user edited a shell rc file that the current shell type reads, which
non-interactive shells (hooks, scheduled jobs, subagent Bash calls) often do not. Resolving the
directory directly keeps a correct install findable from every shell type.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
from pathlib import Path

# Upstream's installer works on macOS and on Linux/WSL (which also needs `unzip` installed).
BUN_INSTALL_COMMAND = "curl -fsSL https://bun.sh/install | bash"
CCUSAGE_INSTALL_COMMAND = "bun add -g ccusage"


@dataclasses.dataclass(frozen=True, slots=True)
class Resolved:
    path: str
    on_path: bool


def bun_bin_dir() -> Path:
    install = os.environ.get("BUN_INSTALL")
    return (Path(install) if install else Path.home() / ".bun") / "bin"


def resolve(name: str, *, bin_dir: Path | None = None) -> Resolved | None:
    """Find *name* on ``PATH`` first, then in bun's global bin directory."""
    found = shutil.which(name)
    if found is not None:
        return Resolved(path=found, on_path=True)
    candidate = (bin_dir if bin_dir is not None else bun_bin_dir()) / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return Resolved(path=str(candidate), on_path=False)
    return None
