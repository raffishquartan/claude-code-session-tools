"""Resolve a `ccmsg send --to-session` reference to a full session uuid."""
from __future__ import annotations

import re
from collections.abc import Callable

from cc_session_tools.lib.sessions_db import SessionRow

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_UUID_PREFIX = re.compile(r"[0-9a-f]{8}[0-9a-f-]*")
_SESSION_NAME = re.compile(r"\d{8}-\S+")

_FORMS = (
    "a session uuid, a session name (YYYYMMDD-<tag>) or a uuid prefix of at least 8 hex characters"
)


class SessionRefError(ValueError):
    """The reference cannot be resolved to exactly one session."""


def resolve_session_ref(
    ref: str,
    *,
    find_by_name: Callable[[str], list[SessionRow]],
    find_by_prefix: Callable[[str], list[SessionRow]],
) -> str:
    """Return the full lowercase uuid `ref` names.

    A canonical uuid is returned as given: the sessions store only records sessions opened
    through ccd/ccr, so a valid uuid it has not seen must still be addressable. A session name is
    tried before a uuid prefix because a date-prefixed name can also read as hex."""
    ref = ref.strip().lower()
    if _UUID.fullmatch(ref):
        return ref

    is_name = _SESSION_NAME.fullmatch(ref) is not None
    is_prefix = _UUID_PREFIX.fullmatch(ref) is not None
    if not is_name and not is_prefix:
        raise SessionRefError(f"{ref!r} is not {_FORMS}")

    rows = find_by_name(ref) if is_name else []
    if not rows and is_prefix:
        rows = find_by_prefix(ref)
    if not rows:
        raise SessionRefError(
            f"no session matches {ref!r}; the sessions store only knows sessions opened with "
            "ccd or ccr, so pass the full uuid for any other session"
        )
    uuids = {row.uuid for row in rows}
    if len(uuids) > 1:
        lines = "\n".join(
            f"  {row.uuid[:8]}  {row.basename}  {row.project_dir}"
            for row in sorted(rows, key=lambda r: (r.uuid, str(r.project_dir)))
        )
        raise SessionRefError(
            f"{ref!r} matches {len(uuids)} sessions; retry with a full uuid:\n{lines}"
        )
    return next(iter(uuids))
