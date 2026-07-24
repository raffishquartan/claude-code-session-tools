"""Stop hook: records a session-activity sentinel into sessions.db.

Fires via Stop event (after each Claude response, not once per session).
Upserts the sessions table's last_active timestamp so `ccs --order-by active`
can sort sessions by recency of Claude activity without a filesystem walk.
Never blocks, never warns.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from cc_session_tools.lib import sessions_db


def main(argv: list[str] | None = None) -> int:
    session_dir_str = os.environ.get("CLD_SESSION_DIR", "")
    if session_dir_str:
        session_dir_path = Path(session_dir_str)
        if session_dir_path.parent.name == "cc-sessions":
            try:
                sessions_db.touch_last_active(
                    session_dir_path.parent.parent, session_dir_path.name
                )
            except (OSError, sqlite3.Error) as exc:
                print(f"[after-response] Failed to record .last-active: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
