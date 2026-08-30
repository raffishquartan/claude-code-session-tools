"""Is a live `claude` process currently working in a given project (spec: "Process safety").

Fails safe: any error resolving a PID's cwd counts as occupied, never as clear.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _claude_pids() -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-x", "claude"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    # str.split() with no args already splits on whitespace and drops empty
    # tokens, so each token is already stripped — no further .strip() needed.
    return [int(p) for p in out.split() if p.isdigit()]


def _cwd_of_pid(pid: int) -> Path:
    if sys.platform == "darwin":
        # lsof's field-output mode ("man lsof", OUTPUT FOR OTHER PROGRAMS)
        # always emits the PID ('p') field, then whichever other fields were
        # requested — here just the name ('n') field — each on its own line,
        # prefixed by its field character. So scanning for the line that
        # starts with 'n' and stripping that one character is documented
        # lsof behaviour, not a guess.
        out = subprocess.run(
            ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
        for line in out.splitlines():
            if line.startswith("n"):
                return Path(line[1:])
        raise OSError(f"lsof returned no cwd line for pid {pid}")
    return Path(os.readlink(f"/proc/{pid}/cwd"))


def is_occupied(project_root: Path, *, exclude_pid: int | None = None) -> bool:
    """True if any live `claude` process (other than `exclude_pid`) has its cwd
    resolved to exactly `project_root` — the same equality test
    `roots.matched_session_root()` uses elsewhere.
    """
    resolved_root = project_root.resolve()
    for pid in _claude_pids():
        if pid == exclude_pid:
            continue
        try:
            cwd = _cwd_of_pid(pid).resolve()
        except OSError:
            return True  # fail safe, never fail open
        if cwd == resolved_root:
            return True
    return False
