"""The one definition of how CCST renders a UTC time for a human (`yyyy-MM-dd HH:mm UTC`)."""
from __future__ import annotations

import time
from datetime import datetime

HUMAN_UTC_FORMAT = "%Y-%m-%d %H:%M UTC"


def format_utc(moment: datetime) -> str:
    """Render a datetime that is already in UTC."""
    return moment.strftime(HUMAN_UTC_FORMAT)


def format_utc_epoch(epoch: float | None = None) -> str:
    """Render an epoch (seconds) in UTC; the current time when omitted."""
    return time.strftime(HUMAN_UTC_FORMAT, time.gmtime(epoch))
