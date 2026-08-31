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


def _ppid_of(pid: int) -> int | None:
    """`pid`'s parent PID, or None if it can't be determined (already exited, permission error,
    or an unsupported platform) - every failure degrades to None rather than raising, since the
    only caller, `launching_claude_pid`, already treats "can't climb any further" as a normal
    stopping condition, not an error."""
    if sys.platform.startswith("linux"):
        try:
            status = Path(f"/proc/{pid}/status").read_text()
        except OSError:
            return None
        for line in status.splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
        return None
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return int(out) if out.isdigit() else None
    return None


def _comm_of(pid: int) -> str | None:
    """`pid`'s process name (`/proc/<pid>/comm`'s exact contents on Linux - the same field
    `pgrep -x` matches against), or None if it can't be read."""
    if sys.platform.startswith("linux"):
        try:
            return Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:
            return None
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["ps", "-o", "comm=", "-p", str(pid)],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return out or None
    return None


def launching_claude_pid(start_pid: int, *, max_hops: int = 5) -> int | None:
    """The nearest ancestor of `start_pid` (inclusive) whose process name is `claude`, or None if
    none is found within `max_hops` steps.

    Exists because a hook's `command` is not run as a direct child of the `claude` process: Claude
    Code spawns every command hook via `/bin/sh -c "<command>"`, and on any system where `/bin/sh`
    is dash rather than bash - the Debian/Ubuntu/WSL2 default - dash does not replace itself with
    the command it's running the way bash does for a single simple `-c` script. That leaves a real
    `sh` process between `claude` and the hook's own process, confirmed empirically (not assumed)
    by installing a diagnostic `SessionStart` hook in a scratch project and inspecting
    `/proc/<pid>/{comm,status}` for the whole ancestor chain: `ccst` (the hook) -> `sh -c ...` ->
    `claude` -> the shell that launched it, exactly two hops from `os.getppid()`'s immediate
    parent to the real `claude` process, not one. A caller that naively used `os.getppid()` as
    "the launching claude process's PID" (as this hook's `main()` used to) would therefore be
    excluding the wrong PID from `is_occupied()`'s check every time - one that will never actually
    appear in `_claude_pids()`'s `pgrep -x claude` results - so the exclusion silently never
    matches anything, and a SessionStart hook checking its own just-started project reports it as
    occupied by itself on every single invocation.

    Bounded to `max_hops` (comfortably above the 2 hops seen in the confirmed case, to tolerate
    extra wrapping such as a `timeout` command) so an unrecognised process-tree shape degrades to
    None rather than looping forever; a caller receiving None should exclude nothing, matching
    this module's existing bias toward reporting occupied over silently under-excluding.

    Unconfirmed on macOS's own `/bin/sh` (Apple's bash 3.2 build) - it may already perform the
    single-command replacement bash does elsewhere, making the extra hop unnecessary there; this
    function is safe either way since it stops as soon as it finds a `claude` ancestor, however
    many hops that takes."""
    pid = start_pid
    for _ in range(max_hops):
        if _comm_of(pid) == "claude":
            return pid
        parent = _ppid_of(pid)
        if parent is None or parent == pid:
            return None
        pid = parent
    return None


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
