"""Is a live `claude` process currently working in a given project (spec: "Process safety").

Fails safe: any error enumerating `claude` processes, or resolving a PID's cwd, counts as
occupied, never as clear - see is_occupied()'s single try/except around both steps. Letting
either failure fall through to "not occupied" would be indistinguishable from the legitimate
"no claude process right now" case to every caller, which is exactly the false "clear to
rehydrate" signal this module exists to prevent.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _claude_pids() -> list[int]:
    """Every running `claude` process's PID. Raises (OSError, subprocess.SubprocessError) on any
    genuine failure to enumerate them - does not swallow these into an empty list, since an
    empty list here is indistinguishable from "pgrep ran fine and found nothing" to is_occupied().
    A *clean* "no matches" from pgrep itself (exit code 1, empty stdout, no exception) is not a
    failure and correctly produces []."""
    out = subprocess.run(
        ["pgrep", "-x", "claude"],
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout
    # str.split() with no args already splits on whitespace and drops empty
    # tokens, so each token is already stripped — no further .strip() needed.
    return [int(p) for p in out.split() if p.isdigit()]


def _cwd_of_pid(pid: int) -> Path:
    """pid's current working directory. Raises (OSError, subprocess.SubprocessError) on any
    failure - callers must catch both, not just OSError (a nonzero lsof exit, e.g. the ordinary
    race where the process has already exited, raises CalledProcessError, not OSError)."""
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
    if sys.platform.startswith("linux"):
        return Path(os.readlink(f"/proc/{pid}/cwd"))
    # A third platform (e.g. Windows-native Python, not WSL) has no /proc and isn't darwin -
    # raise explicitly rather than silently falling into the Linux branch and failing with a
    # confusing FileNotFoundError. Still caught by is_occupied()'s fail-safe handler below.
    raise OSError(f"occupancy check not implemented for platform {sys.platform!r}")


def is_occupied(project_root: Path, *, exclude_pid: int | None = None) -> bool:
    """True if any live `claude` process (other than `exclude_pid`) has its cwd resolved to
    exactly `project_root` - matching Claude Code's own behaviour of keeping a session's cwd
    fixed at the project root for its whole lifetime (see e.g. ccd.py's os.chdir(real_pwd)),
    never a cc-sessions/<tag>/ subdirectory."""
    resolved_root = project_root.resolve()
    try:
        pids = _claude_pids()
    except (OSError, subprocess.SubprocessError):
        return True  # can't even enumerate claude processes - fail safe, never fail open
    for pid in pids:
        if pid == exclude_pid:
            continue
        try:
            cwd = _cwd_of_pid(pid).resolve()
        except (OSError, subprocess.SubprocessError):
            return True  # fail safe, never fail open
        if cwd == resolved_root:
            return True
    return False
