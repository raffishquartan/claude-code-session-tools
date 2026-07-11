"""Adapter over cccs_hooks.telemetry: write one fires.jsonl line per sweep
action (hook='catchup'), and read recent catchup lines back for ``ccsched
status``. Reuses the shared telemetry ledger; does not create a new stream."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from cccs_hooks.telemetry import _DEFAULT_HOOKS_DIR, TelemetryEntry, log_event


class LedgerEvent(str, Enum):
    LAUNCH = "launch"
    RUN = "run"
    BACKFILL = "backfill"
    SKIP_EXPIRED = "skip_expired"
    DEFER = "defer"
    FAIL = "fail"
    SUSPEND = "suspend"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    job_id: str
    event: LedgerEvent
    owed: int
    ran: int
    exit_code: int | None
    duration_ms: int
    error: str | None
    consecutive_failures: int = 0


def _hooks_dir() -> Path:
    """The telemetry log directory: the CCCS_HOOKS_DIR override when set,
    else the telemetry module's default. Single source of truth so the read
    path (``_all_catchup_rows``) and the write path (``log_event``) can never
    point at different directories."""
    raw = os.environ.get("CCCS_HOOKS_DIR")
    return Path(raw) if raw else _DEFAULT_HOOKS_DIR


def record(entry: LedgerEntry) -> None:
    """Append one catchup event line to fires.jsonl. Never raises."""
    verdict = json.dumps(
        {
            "job_id": entry.job_id,
            "event": entry.event.value,
            "owed": entry.owed,
            "ran": entry.ran,
            "exit_code": entry.exit_code,
            "duration_ms": entry.duration_ms,
            "error": entry.error,
            "consecutive_failures": entry.consecutive_failures,
        },
        separators=(",", ":"),
    )
    log_event(
        TelemetryEntry(
            hook="catchup",
            event="",
            tool="",
            session_id="",
            cwd_short="",
            decision="annotate",
            cache="none",
            verdict=verdict,
            input_hash="",
        ),
        hooks_dir=_hooks_dir(),
    )


def _all_catchup_rows() -> list[dict[str, object]]:
    """Every catch-up entry in fires.jsonl, oldest-first, with verdict unpacked."""
    fires = _hooks_dir() / "fires.jsonl"
    if not fires.is_file():
        return []
    rows: list[dict[str, object]] = []
    for raw in fires.read_text().splitlines():
        try:
            line: dict[str, object] = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if line.get("hook") != "catchup":
            continue
        try:
            detail: dict[str, object] = json.loads(str(line.get("verdict", "{}")))
        except json.JSONDecodeError:
            detail = {}
        rows.append({"ts": line.get("ts"), "hook": "catchup", **detail})
    return rows


def read_recent(job_id: str | None = None, *, limit: int = 50) -> list[dict[str, object]]:
    """Return up to ``limit`` recent catchup rows, optionally filtered by job_id."""
    rows = _all_catchup_rows()
    if job_id is not None:
        rows = [r for r in rows if r.get("job_id") == job_id]
    return rows[-limit:]


def read_since(offset: int) -> tuple[list[dict[str, object]], int]:
    """Catch-up entries after the integer line-count ``offset`` (count of
    catch-up entries previously surfaced), plus the new offset.

    Used by the surface/reap phase (§9.3). Filters to catch-up entries first,
    so unrelated hook lines in the shared ledger never shift the offset.
    """
    rows = _all_catchup_rows()
    return rows[offset:], len(rows)


def current_offset() -> int:
    """The line-count offset of the current end of the catch-up ledger. Used to
    seed a brand-new session's cursor (§9.3) so its first digest reflects only
    activity from this point forward, not pre-existing history."""
    return len(_all_catchup_rows())
