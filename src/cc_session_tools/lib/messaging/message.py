# src/cc_session_tools/lib/messaging/message.py
"""Message file format: a typed dataclass, YAML-frontmatter round-trip, and
atomic writes. The frontmatter is the single source of truth for routing and
state; the body is free-form markdown."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, get_args

import yaml

ToKind = Literal["session", "project", "description"]
Status = Literal["sent", "read", "claimed", "archived"]

_VALID_TO_KIND: frozenset[str] = frozenset(get_args(ToKind))
_VALID_STATUS: frozenset[str] = frozenset(get_args(Status))

_FRONTMATTER_KEYS = (
    "id", "schema", "from_project", "from_session", "from_uuid",
    "to_kind", "to_value", "to_location", "subject", "sent_at",
    "status", "read_at", "read_by_uuid", "read_by_session",
    "claimed_at", "receipt_shown", "thread", "attachments",
)


@dataclass
class Message:
    id: str
    schema: int
    from_project: str
    from_session: str
    from_uuid: str
    to_kind: ToKind
    to_value: str
    to_location: str
    subject: str
    sent_at: str
    status: Status
    read_at: str | None
    read_by_uuid: str | None
    read_by_session: str | None
    claimed_at: str | None
    receipt_shown: bool
    thread: str | None
    attachments: list[str] = field(default_factory=list)
    body: str = ""


def serialise(message: Message) -> str:
    data = asdict(message)
    body = data.pop("body")
    front = {k: data[k] for k in _FRONTMATTER_KEYS}
    yaml_block = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
    return f"---\n{yaml_block}---\n\n{body}"


def parse(text: str) -> Message:
    if not text.startswith("---\n"):
        raise ValueError("message file has no YAML frontmatter")
    rest = text[len("---\n"):]
    end = rest.find("\n---\n")
    if end == -1:
        raise ValueError("message frontmatter is not terminated by '---'")
    yaml_block = rest[:end]
    body = rest[end + len("\n---\n"):].lstrip("\n")
    front = yaml.safe_load(yaml_block)
    if not isinstance(front, dict):
        raise ValueError("message frontmatter is not a mapping")
    # parse() is the boundary where untyped on-disk YAML becomes a typed
    # Message, so it validates here: a missing required key becomes a ValueError
    # (never a leaked KeyError), and to_kind/status must be within their Literal
    # sets so internals can trust the dataclass contract.
    try:
        if front["to_kind"] not in _VALID_TO_KIND:
            raise ValueError(f"invalid to_kind: {front['to_kind']!r}")
        if front["status"] not in _VALID_STATUS:
            raise ValueError(f"invalid status: {front['status']!r}")
        return Message(
            id=str(front["id"]),
            schema=int(front["schema"]),
            from_project=str(front["from_project"]),
            from_session=str(front["from_session"]),
            from_uuid=str(front["from_uuid"]),
            to_kind=front["to_kind"],
            to_value=str(front["to_value"]),
            to_location=str(front["to_location"]),
            subject=str(front["subject"]),
            sent_at=str(front["sent_at"]),
            status=front["status"],
            read_at=front["read_at"],
            read_by_uuid=front["read_by_uuid"],
            read_by_session=front["read_by_session"],
            claimed_at=front["claimed_at"],
            receipt_shown=bool(front["receipt_shown"]),
            thread=front["thread"],
            attachments=list(front.get("attachments") or []),
            body=body,
        )
    except KeyError as exc:
        raise ValueError(f"missing required frontmatter field: {exc}") from exc


def write_text_atomic(path: Path, text: str) -> None:
    """Generalised atomic text write (``.tmp``-swap), mirroring
    ``hooks_install.write_json_atomic``."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_atomic(path: Path, message: Message) -> None:
    write_text_atomic(path, serialise(message))
