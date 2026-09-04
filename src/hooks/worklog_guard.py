"""PreCompact hook: blocks /compact if the session's WORKLOG.md is stale.

Fires via PreCompact event, registered with matcher "manual" only — it never
runs for automatic system-triggered compaction, which can happen mid-task in
an unattended session where nobody is present to act on a block. Manual
/compact is exactly the moment where a WORKLOG staleness check is worth
blocking on rather than just warning to stderr: the user is about to
summarise-and-discard context, so un-persisted progress is at risk right now.

Only acts for sessions started via `ccd`/`ccr` (CLD_SESSION_DIR set) that
already have a WORKLOG.md — it never creates one. Escape hatch:
CCCS_ALLOW_STALE_WORKLOG=1 bypasses the block.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

DEFAULT_STALE_SECONDS = 3600


def check_worklog_stale(session_dir: Path, stale_seconds: int = DEFAULT_STALE_SECONDS) -> str | None:
    """Return a block reason if session_dir's WORKLOG.md exists and is stale."""
    worklog = session_dir / "working" / "WORKLOG.md"
    if not worklog.exists():
        return None
    age = time.time() - worklog.stat().st_mtime
    if age <= stale_seconds:
        return None
    return (
        f"WORKLOG.md at {worklog} was last updated {int(age // 60)} minutes ago. "
        "Update it with a summary of this session's progress before compacting "
        "(or set CCCS_ALLOW_STALE_WORKLOG=1 to bypass)."
    )


def main(argv: list[str] | None = None) -> int:
    sys.stdin.read()

    if os.environ.get("CCCS_ALLOW_STALE_WORKLOG", "0") == "1":
        return 0

    session_dir_str = os.environ.get("CLD_SESSION_DIR", "")
    if not session_dir_str:
        return 0

    reason = check_worklog_stale(Path(session_dir_str))
    if reason is None:
        return 0

    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
