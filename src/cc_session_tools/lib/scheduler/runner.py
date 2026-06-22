"""Per-job subprocess runner: run an argv with a hard timeout, capturing
output and wall duration. Never raises on a non-zero exit or a timeout — the
worker decides what to record. Also a detached-spawn primitive used by the
launch phase to start the background worker off the session critical path."""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class RunOutcome:
    exit_code: int | None  # None when timed out
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool


def spawn_detached(argv: tuple[str, ...] | list[str]) -> int:
    """Launch a fully-detached background process and return its pid.

    The child is put in its own session (start_new_session=True) so it
    survives the parent hook exiting, with std streams sent to os.devnull.
    Returns immediately; does not wait (§4, §9.1). Cross-checked for WSL2
    (§17.3): start_new_session=True detaches the child from the session's
    process group so it survives the hook process exiting.
    """
    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    return proc.pid


def run_command(argv: tuple[str, ...] | list[str], timeout: timedelta) -> RunOutcome:
    """Run argv with a hard timeout, capturing stdout/stderr and wall duration.

    Returns a RunOutcome. Never raises on non-zero exit or timeout.
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout.total_seconds(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return RunOutcome(
            exit_code=None,
            stdout=stdout,
            stderr=stderr,
            duration_ms=elapsed,
            timed_out=True,
        )
    elapsed = int((time.monotonic() - start) * 1000)
    return RunOutcome(
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_ms=elapsed,
        timed_out=False,
    )
