"""record_group naming convention and the derived SQL-identifier transforms (spec §4.2/§4.3).

record_group is a caller-facing name (e.g. 'key-events'); ext_<record_group> is never typed by
a caller directly — its underscore form is purely an internal table-naming detail.
"""
from __future__ import annotations

import re

_RECORD_GROUP_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# The base `records` table's fixed column set (spec §4.2). Public — this is the single source
# of truth for "what is a base column"; repository.py's schema_show_columns (Task 9) and its
# query-builder's base-vs-extension field resolution (Task 14) both import this rather than
# each maintaining their own copy, so the two can't silently drift apart.
BASE_RECORD_COLUMNS: tuple[str, ...] = (
    "id", "record_group", "content", "file_path",
    "created_at", "updated_at", "version", "deleted_at",
)


def validate_record_group(record_group: str) -> None:
    """Raise ValueError unless record_group is lowercase letters/digits/hyphens only, with no
    leading/trailing/doubled hyphen (spec §4.2's ^[a-z0-9]+(-[a-z0-9]+)*$)."""
    if not _RECORD_GROUP_RE.match(record_group):
        raise ValueError(
            f"invalid record_group {record_group!r}: must match "
            f"^[a-z0-9]+(-[a-z0-9]+)*$ (lowercase letters, digits, single hyphens only)"
        )


def extension_table_name(record_group: str) -> str:
    """ext_<record_group> with every hyphen replaced by an underscore (spec §4.3 bug fix) —
    the only place this transform happens; callers never type the underscore form."""
    validate_record_group(record_group)
    return "ext_" + record_group.replace("-", "_")


_RESERVED_FIELD_NAMES = frozenset(BASE_RECORD_COLUMNS) | {"record_id"}


def validate_field_name(field_name: str) -> None:
    """Raise ValueError unless field_name is safe to interpolate as a SQL identifier
    (extension-table column names cannot be bound parameters — see plan Decision 2) and
    doesn't collide with a base records column or the ext table's own record_id PK — a
    colliding extension field would silently overwrite the base column's value once
    get/list/query flatten base + extension fields into one dict."""
    if not _FIELD_NAME_RE.match(field_name):
        raise ValueError(
            f"invalid field name {field_name!r}: must match ^[a-z][a-z0-9_]*$"
        )
    if field_name in _RESERVED_FIELD_NAMES:
        raise ValueError(
            f"invalid field name {field_name!r}: collides with a base records column"
        )
