"""Correctness-keyed garbage-collection report for the per-session-uuid data
stores that accumulate one row/directory per session forever, with no existing
cleanup code:

  ccsched.db  reconcile_throttle(session_uuid, ...)  (reconcile throttle marker)
  ccsched.db  cursors(session_uuid, offset)           (scheduler catch-up cursor)
  ccmsg.db    cursors(session_uuid, partition, ...)   (messaging delivery cursor;
                                                        N rows per session, one
                                                        per partition)
  sessions.db session_tags(uuid, tag, updated_at)     (session-tag index)
  ~/.claude/session-env/<uuid>/                        (harness-created, not by
                                                        this repo, but same rule
                                                        applies — still a flat
                                                        directory, not migrated)

An entry is orphaned iff its uuid has no matching transcript at
``~/.claude/projects/*/<uuid>.jsonl`` — i.e. the owning session is provably
gone. Dormancy length is never the deciding factor: a session can legitimately
be resumed weeks after its last activity, so only transcript existence counts.

This module is report-only by design (mirrors the report/execute split in
``cccs_hooks/telemetry_trim.py``, except only the report half is built): it
enumerates and counts orphans but never deletes or modifies anything. Exposed
as ``ccst gc report``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib import db as _db
from cc_session_tools.lib.messaging.store import store_root as _default_messages_root
from cc_session_tools.lib.scheduler.store import scheduler_dir as _default_scheduler_dir
from cc_session_tools.lib.sessions_db import default_db_path as _default_sessions_db_path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_SESSION_ENV_DIR = Path.home() / ".claude" / "session-env"

# Verified table/uuid-column names (Phase 2/3/4 merged source — see the data-store
# uplift Phase 7 plan, Task 3). ccsched.db keeps reconcile-throttle and catch-up
# cursor as TWO SEPARATE tables.
_SCHEDULER_CURSORS_TABLE = "cursors"               # ccsched.db (Phase 3)
_SCHEDULER_RECONCILE_TABLE = "reconcile_throttle"  # ccsched.db (Phase 3)
_MESSAGES_CURSOR_TABLE = "cursors"                 # ccmsg.db, composite PK (session_uuid, partition) (Phase 2)
_SESSION_TAGS_TABLE = "session_tags"               # sessions.db, uuid-keyed (Phase 4)


@dataclass(frozen=True, slots=True)
class StoreReport:
    """Orphan count for one uuid-keyed store."""

    name: str
    total: int
    orphaned_uuids: tuple[str, ...]

    @property
    def orphaned(self) -> int:
        return len(self.orphaned_uuids)


@dataclass(frozen=True, slots=True)
class GcReport:
    """Full report across all uuid-keyed stores."""

    known_uuid_count: int
    stores: tuple[StoreReport, ...]

    @property
    def total_entries(self) -> int:
        return sum(s.total for s in self.stores)

    @property
    def total_orphaned(self) -> int:
        return sum(s.orphaned for s in self.stores)


def known_session_uuids(projects_dir: Path) -> set[str]:
    """Return every session uuid with a transcript under
    ``<projects_dir>/*/<uuid>.jsonl``.

    Still a filesystem walk, not a sessions.db query, even though sessions.db
    (Phase 4) now indexes most of the same uuids — left as a directory walk
    in Phase 7 deliberately (out of scope to change here); a good follow-up
    candidate once sessions.db is confirmed authoritative for "which session
    uuids exist," since a transcript being deleted by hand would otherwise
    silently desync the two.
    """
    if not projects_dir.is_dir():
        return set()
    return {p.stem for p in projects_dir.glob("*/*.jsonl")}


def _scheduler_cursor_uuids_db(ccsched_db_path: Path) -> dict[str, Path]:
    """Session uuids with a catch-up-cursor row in ccsched.db (Phase 3's
    ``cursors`` table — row presence is the dimension)."""
    if not ccsched_db_path.exists():
        return {}
    conn = _db.connect(ccsched_db_path, readonly=True)
    try:
        rows = conn.execute(
            f"SELECT session_uuid FROM {_SCHEDULER_CURSORS_TABLE}"
        ).fetchall()
    finally:
        conn.close()
    return {row["session_uuid"]: ccsched_db_path for row in rows}


def _scheduler_reconcile_uuids_db(ccsched_db_path: Path) -> dict[str, Path]:
    """Session uuids with a reconcile-throttle row in ccsched.db (Phase 3's
    ``reconcile_throttle`` table — a table kept SEPARATE from ``cursors``, so
    this is an independent dimension: a session can have a cursor but no
    throttle marker, and vice versa)."""
    if not ccsched_db_path.exists():
        return {}
    conn = _db.connect(ccsched_db_path, readonly=True)
    try:
        rows = conn.execute(
            f"SELECT session_uuid FROM {_SCHEDULER_RECONCILE_TABLE}"
        ).fetchall()
    finally:
        conn.close()
    return {row["session_uuid"]: ccsched_db_path for row in rows}


def _messages_cursor_uuids_db(ccmsg_db_path: Path) -> dict[str, Path]:
    """Distinct session uuids with a cursor row in ccmsg.db. The ``cursors``
    table is composite-keyed ``(session_uuid, partition)`` (Phase 2), so one
    session yields N rows (one per partition); SELECT DISTINCT collapses them
    to one entry so ``store.total`` counts distinct sessions, not raw rows."""
    if not ccmsg_db_path.exists():
        return {}
    conn = _db.connect(ccmsg_db_path, readonly=True)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT session_uuid FROM {_MESSAGES_CURSOR_TABLE}"
        ).fetchall()
    finally:
        conn.close()
    return {row["session_uuid"]: ccmsg_db_path for row in rows}


def _sessions_db_uuids(sessions_db_path: Path) -> dict[str, Path]:
    """Session uuids with a row in sessions.db's ``session_tags`` table
    (Phase 4). NOT the ``sessions`` table — that is keyed by
    ``(project_dir, basename)`` and has no uuid column; uuids live only in
    ``session_tags(uuid, tag, updated_at)``. Note the column is ``uuid``,
    not ``session_uuid``."""
    if not sessions_db_path.exists():
        return {}
    conn = _db.connect(sessions_db_path, readonly=True)
    try:
        rows = conn.execute(f"SELECT uuid FROM {_SESSION_TAGS_TABLE}").fetchall()
    finally:
        conn.close()
    return {row["uuid"]: sessions_db_path for row in rows}


def _session_env_uuids(session_env_dir: Path) -> dict[str, Path]:
    """``<session_env_dir>/<uuid>/`` — one directory per session, harness-owned."""
    out: dict[str, Path] = {}
    if not session_env_dir.is_dir():
        return out
    for p in session_env_dir.iterdir():
        if p.is_dir():
            out[p.name] = p
    return out


def _store_report(name: str, entries: dict[str, Path], known_uuids: set[str]) -> StoreReport:
    orphaned = tuple(sorted(uuid for uuid in entries if uuid not in known_uuids))
    return StoreReport(name=name, total=len(entries), orphaned_uuids=orphaned)


def build_report(
    *,
    projects_dir: Path | None = None,
    scheduler_dir: Path | None = None,
    messages_root: Path | None = None,
    session_env_dir: Path | None = None,
    sessions_dir: Path | None = None,
) -> GcReport:
    """Enumerate known session uuids and every uuid-keyed store, and compute
    the orphan set per store. Read-only: never deletes or modifies anything.

    Each directory can be overridden explicitly (used by tests and by the
    CLI's override flags); when omitted, each store resolves its own default
    the same way its owning module does (respecting that module's env-var
    override, e.g. ``CC_SCHEDULER_DIR`` / ``CCST_MESSAGES_ROOT`` /
    ``CCST_SESSIONS_DIR``).
    """
    projects_dir = projects_dir if projects_dir is not None else DEFAULT_PROJECTS_DIR
    scheduler_dir = scheduler_dir if scheduler_dir is not None else _default_scheduler_dir()
    messages_root = messages_root if messages_root is not None else _default_messages_root()
    session_env_dir = (
        session_env_dir if session_env_dir is not None else DEFAULT_SESSION_ENV_DIR
    )
    # default_db_path() returns the full sessions.db path; take its parent so
    # `sessions_dir` stays a directory, consistent with the --sessions-dir CLI flag.
    sessions_dir = sessions_dir if sessions_dir is not None else _default_sessions_db_path().parent

    known = known_session_uuids(projects_dir)

    stores = (
        _store_report(
            "scheduler-reconcile-markers",
            _scheduler_reconcile_uuids_db(scheduler_dir / "ccsched.db"),
            known,
        ),
        _store_report(
            "scheduler-cursors",
            _scheduler_cursor_uuids_db(scheduler_dir / "ccsched.db"),
            known,
        ),
        _store_report(
            "messages-cursors",
            _messages_cursor_uuids_db(messages_root / "ccmsg.db"),
            known,
        ),
        _store_report(
            "session-env",
            _session_env_uuids(session_env_dir),
            known,
        ),
        _store_report(
            "sessions-index",
            _sessions_db_uuids(sessions_dir / "sessions.db"),
            known,
        ),
    )
    return GcReport(known_uuid_count=len(known), stores=stores)


def format_report(report: GcReport) -> str:
    """Render a ``GcReport`` as a fixed-width table, e.g. for ``ccst gc report``."""
    lines = [f"Known session uuids (transcripts found): {report.known_uuid_count}", ""]

    name_w = max(len(s.name) for s in report.stores)
    header = (f"{'Store':<{name_w}}", f"{'Total':>6}", f"{'Orphaned':>8}")
    lines.append("  ".join(header))
    lines.append("  ".join(("-" * name_w, "-" * 6, "-" * 8)))
    for s in report.stores:
        lines.append(f"{s.name:<{name_w}}  {s.total:>6}  {s.orphaned:>8}")

    lines.append("")
    lines.append(
        f"Total: {report.total_entries} entries, {report.total_orphaned} orphaned"
    )
    lines.append("Report-only — no files were deleted or modified.")
    return "\n".join(lines)
