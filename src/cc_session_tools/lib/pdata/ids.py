"""pdata record ids: canonical lowercase hyphenated UUID strings.

New records get a random UUIDv4, so two machines writing independently can never pick the same
id. Records that are unique by meaning (the session-output watermark, each registered
session-output file) and records rewritten by `ccst pdata migrate-uuid` get a UUIDv5 over
`"<project>:<key>"` in RECORD_ID_NAMESPACE, so every machine derives the same id from the same
input with no coordination.
"""
from __future__ import annotations

import re
import uuid
from typing import Final

# Fixed for the life of the product: changing it would give every deterministic id a new value
# and make two machines on different releases disagree about the same record.
RECORD_ID_NAMESPACE: Final = uuid.UUID("40090682-d73d-4958-a608-9c9960620f31")

_CANONICAL_UUID_RE: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def new_record_id() -> str:
    """A fresh random record id."""
    return str(uuid.uuid4())


def deterministic_record_id(project: str, key: str) -> str:
    """The record id every machine derives for `key` in `project`: UUIDv5 over
    `"<project>:<key>"`."""
    return str(uuid.uuid5(RECORD_ID_NAMESPACE, f"{project}:{key}"))


def is_record_id(value: str) -> bool:
    """True iff `value` is a UUID in canonical lowercase hyphenated form - the only form a
    record id is ever stored or accepted in."""
    return _CANONICAL_UUID_RE.fullmatch(value) is not None
