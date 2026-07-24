# src/cc_session_tools/lib/messaging/retention.py
"""Opportunistic retention: archive read/claimed messages older than 14 days.

Archiving is a status flip (never a delete), done in one atomic statement so a
concurrent sweep or claim can neither crash it nor lose claim metadata (R1).
Unread messages never expire. Called from deliver with bounded per-sweep cost."""
from __future__ import annotations

from datetime import datetime, timedelta

from cc_session_tools.lib.messaging import repository

_RETENTION_DAYS = 14


def archive_old(partition: str, now: datetime) -> list[str]:
    """Archive eligible messages in ``partition``. Returns the archived ids."""
    cutoff = (now - timedelta(days=_RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return repository.archive_aged(partition, cutoff)
