# src/cc_session_tools/lib/messaging/move_safety.py
"""Rename/move safety for the message store, called by the move-session skill.

uuid routing means no message is ever orphaned by a rename; these helpers keep
the cosmetic display tag fresh and give the move flow an explicit cursor hook."""
from __future__ import annotations

from cc_session_tools.lib.messaging import cursor as cursor_mod
from cc_session_tools.lib.messaging import repository


def refresh_display_tags(*, uuid: str, new_tag: str) -> int:
    """Update from_session / read_by_session for pending (non-archived) messages
    referencing ``uuid``. Returns the count updated (one targeted UPDATE)."""
    return repository.refresh_display_tags(uuid, new_tag)


def relocate_cursor(*, uuid: str, old_partition: str, new_partition: str) -> None:
    """The cursor is uuid-keyed, so it survives a project move unchanged. This
    explicit call site exists for the move-session flow and future rekeying."""
    _ = (old_partition, new_partition)  # currently a no-op by design
    cursor_mod.save(uuid, cursor_mod.load(uuid))
