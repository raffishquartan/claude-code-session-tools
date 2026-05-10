from __future__ import annotations

import os
import sys


def is_debug() -> bool:
    return os.environ.get("CCX_DEBUG", "").strip() not in ("", "0")


def debug(*args: object) -> None:
    if is_debug():
        print("[CCX_DEBUG]", *args, file=sys.stderr)
