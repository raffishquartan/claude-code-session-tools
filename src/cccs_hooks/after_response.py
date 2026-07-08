"""Stop hook: records a session-activity sentinel.

Fires via Stop event (after each Claude response, not once per session).
Touches a `.last-active` sentinel file so `ccs --order-by active` can sort
sessions by recency of Claude activity. Never blocks, never warns.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    session_dir_str = os.environ.get("CLD_SESSION_DIR", "")
    if session_dir_str:
        try:
            Path(session_dir_str).joinpath(".last-active").touch()
        except OSError as exc:
            print(f"[after-response] Failed to touch .last-active: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
