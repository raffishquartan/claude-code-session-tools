"""Stop hook: WORKLOG staleness and uncommitted-changes nudge.

Fires via Stop event (after each Claude response). Sends warnings to stderr
when work may have been left in an unfinished state. Never blocks.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


def check_uncommitted(cwd: Path) -> str | None:
    """Return a warning message if the cwd is a feature branch with uncommitted changes."""
    try:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if branch_result.returncode != 0:
            return None
        branch = branch_result.stdout.strip()
        if not branch.startswith("f/"):
            return None
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if status_result.returncode != 0:
            return None
        if status_result.stdout.strip():
            return f"⚠ [session-end] Uncommitted changes on {branch} in {cwd.name}. Consider committing before closing the session."
        return None
    except Exception:
        return None


def check_worklog_stale(cwd: Path, stale_seconds: int = 3600) -> str | None:
    """Return a warning message if a WORKLOG.md in cc-sessions/ hasn't been updated recently."""
    try:
        worklogs = sorted(cwd.glob("cc-sessions/*/working/WORKLOG.md"))
        if not worklogs:
            return None
        worklog = worklogs[-1]
        age = time.time() - worklog.stat().st_mtime
        if age > stale_seconds:
            return f"⚠ [session-end] WORKLOG.md at {worklog.relative_to(cwd)} was last updated {int(age // 60)} minutes ago. Consider updating it before closing."
        return None
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        data: dict[str, object] = json.loads(raw)
    except json.JSONDecodeError:
        data = {}

    cwd_str = str(data.get("cwd", ""))
    cwd = Path(cwd_str) if cwd_str else Path.cwd()

    for check_fn in (check_uncommitted, check_worklog_stale):
        warning = check_fn(cwd)
        if warning:
            print(warning, file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
