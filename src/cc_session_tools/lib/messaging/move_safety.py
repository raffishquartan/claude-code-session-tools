# src/cc_session_tools/lib/messaging/move_safety.py
"""Rename/move safety for the message store, called by the move-session skill.

uuid routing means no message is ever orphaned by a rename; these helpers keep
the cosmetic display tag fresh and give the move flow an explicit cursor hook."""
from __future__ import annotations

from cc_session_tools.lib.messaging.message import safe_parse, write_atomic
from cc_session_tools.lib.messaging.service import _iter_message_files
from cc_session_tools.lib.messaging import cursor as cursor_mod


def refresh_display_tags(*, uuid: str, new_tag: str) -> int:
    """Update from_session / read_by_session display tags for pending messages
    referencing ``uuid``. Returns the count updated."""
    updated = 0
    for path in _iter_message_files():
        # Skip (and log) a malformed file so one bad message never aborts the
        # refresh mid-sweep and leaves a partial rename.
        message = safe_parse(path)
        if message is None:
            continue
        if message.status == "archived":
            continue
        changed = False
        if message.from_uuid == uuid and message.from_session != new_tag:
            message.from_session = new_tag
            changed = True
        if message.read_by_uuid == uuid and message.read_by_session != new_tag:
            message.read_by_session = new_tag
            changed = True
        if changed:
            write_atomic(path, message)
            updated += 1
    return updated


def relocate_cursor(*, uuid: str, old_partition: str, new_partition: str) -> None:
    """The cursor is uuid-keyed, so it survives a project move unchanged. This
    explicit call site exists for the move-session flow and future rekeying."""
    _ = (old_partition, new_partition)  # currently a no-op by design
    cursor_mod.save(uuid, cursor_mod.load(uuid))
