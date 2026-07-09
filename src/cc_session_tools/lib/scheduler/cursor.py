"""Per-session surfacing cursor (§9.3): the last-surfaced position in the
catch-up ledger for one session uuid. Stored at <scheduler-dir>/.cursors/
<uuid>.json via atomic .tmp-swap. Per-session by design; cross-session dedup is
a non-goal."""
from __future__ import annotations

import json
from pathlib import Path

from cc_session_tools.lib.scheduler import ledger
from cc_session_tools.lib.scheduler.state import scheduler_dir


def _cursor_dir() -> Path:
    return scheduler_dir() / ".cursors"


def _cursor_path(uuid: str) -> Path:
    return _cursor_dir() / f"{uuid}.json"


def read_cursor(uuid: str) -> int:
    path = _cursor_path(uuid)
    if not path.is_file():
        return 0
    return int(json.loads(path.read_text())["offset"])


def write_cursor(uuid: str, offset: int) -> None:
    target = _cursor_dir()
    target.mkdir(parents=True, exist_ok=True)
    path = _cursor_path(uuid)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"offset": offset}) + "\n")
    tmp.replace(path)


def seed_new_session(uuid: str) -> None:
    """If this session_id has no cursor yet, seed one at the current end of the
    ledger, so its first catchup digest reflects only activity from this point
    forward - not the entire pre-existing ledger history. No-op if a cursor
    already exists (idempotent; safe to call on every hook invocation)."""
    if _cursor_path(uuid).is_file():
        return
    write_cursor(uuid, ledger.current_offset())
