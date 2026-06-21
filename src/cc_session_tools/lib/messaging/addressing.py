# src/cc_session_tools/lib/messaging/addressing.py
"""Decide whether a message is addressed to a given session context.

Identity is always the session uuid (never the display tag). Project matching
uses the project label. Description-addressed messages are advisory candidates
that any session may claim."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from cc_session_tools.lib.messaging.message import Message


class MatchKind(str, Enum):
    NONE = "none"
    RECIPIENT = "recipient"     # auto-read applies
    CANDIDATE = "candidate"     # description-addressed; propose + claim


@dataclass(frozen=True)
class SessionContext:
    uuid: str
    project: str
    partition: str


def targets(message: Message, ctx: SessionContext) -> MatchKind:
    if message.status in ("read", "claimed", "archived"):
        return MatchKind.NONE
    if message.to_kind == "session":
        return MatchKind.RECIPIENT if message.to_value == ctx.uuid else MatchKind.NONE
    if message.to_kind == "project":
        return MatchKind.RECIPIENT if message.to_value == ctx.project else MatchKind.NONE
    return MatchKind.CANDIDATE
