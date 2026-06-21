# src/cc_session_tools/lib/messaging/cursor.py
"""Per-session delivery cursor. Keyed on the stable session uuid so a rename
never resets it. Stores a per-partition high-water id; a message is new to a
session iff its (sortable) id exceeds the stored high-water for its partition."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cc_session_tools.lib.messaging.message import Message
from cc_session_tools.lib.messaging.message import write_text_atomic
from cc_session_tools.lib.messaging.store import cursors_dir


@dataclass(frozen=True)
class Cursor:
    high_water: dict[str, str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> Cursor:
        return cls(high_water={})


def is_new(message: Message, cursor: Cursor) -> bool:
    hw = cursor.high_water.get(message.to_location)
    return hw is None or message.id > hw


def advance(cursor: Cursor, message: Message) -> Cursor:
    hw = dict(cursor.high_water)
    current = hw.get(message.to_location)
    if current is None or message.id > current:
        hw[message.to_location] = message.id
    return Cursor(high_water=hw)


def _path(session_uuid: str) -> Path:
    return cursors_dir() / f"{session_uuid}.json"


def load(session_uuid: str) -> Cursor:
    path = _path(session_uuid)
    if not path.is_file():
        return Cursor.empty()
    # load() is the on-disk boundary: a cursor with a missing/ill-typed
    # high_water map degrades to empty (delivery stays safe because targets()
    # re-checks message status), and ids are coerced to str so is_new() never
    # raises on a tampered file.
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("high_water") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return Cursor.empty()
    return Cursor(high_water={str(k): str(v) for k, v in raw.items()})


def save(session_uuid: str, cursor: Cursor) -> None:
    write_text_atomic(
        _path(session_uuid),
        json.dumps({"high_water": cursor.high_water}, indent=2) + "\n",
    )
