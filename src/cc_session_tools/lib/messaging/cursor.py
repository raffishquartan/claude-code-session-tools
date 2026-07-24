# src/cc_session_tools/lib/messaging/cursor.py
"""Per-session delivery cursor. Keyed on the stable session uuid so a rename
never resets it. Stores a per-partition high-water id; a message is new to a
session iff its (sortable) id exceeds the stored high-water for its partition.
Backed by the cursors table in ccmsg.db."""
from __future__ import annotations

from dataclasses import dataclass, field

from cc_session_tools.lib.messaging import repository
from cc_session_tools.lib.messaging.message import Message


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


def load(session_uuid: str) -> Cursor:
    return Cursor(high_water=repository.load_cursor(session_uuid))


def save(session_uuid: str, cursor: Cursor) -> None:
    repository.save_cursor(session_uuid, cursor.high_water)
