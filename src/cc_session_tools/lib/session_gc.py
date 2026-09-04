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

``build_report``/``format_report`` (``ccst gc report``) are read-only: they
enumerate and count orphans but never delete or modify anything. ``prune``/
``format_prune_report`` (``ccst gc prune``) is the execute half (mirrors the
report/execute split in ``hooks/telemetry_trim.py``) — same orphan
definition, reused unchanged, gated by an explicit ``--execute`` flag and a
per-uuid minimum-age floor (see the note above ``_uuid_age_hours``).
"""
from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cc_session_tools.lib import db as _db
from cc_session_tools.lib.messaging.store import store_root as _default_messages_root
from cc_session_tools.lib.scheduler.state import parse_ts_or_none as _parse_ts_or_none
from cc_session_tools.lib.scheduler.store import scheduler_dir as _default_scheduler_dir
from cc_session_tools.lib.sessions_db import default_db_path as _default_sessions_db_path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_SESSION_ENV_DIR = Path.home() / ".claude" / "session-env"
DEFAULT_MIN_AGE_HOURS = 24.0

# Verified table/uuid-column names (Phase 2/3/4 merged source — see the data-store
# uplift Phase 7 plan, Task 3). ccsched.db keeps reconcile-throttle and catch-up
# cursor as TWO SEPARATE tables.
_SCHEDULER_CURSORS_TABLE = "cursors"               # ccsched.db (Phase 3)
_SCHEDULER_RECONCILE_TABLE = "reconcile_throttle"  # ccsched.db (Phase 3)
_MESSAGES_CURSOR_TABLE = "cursors"                 # ccmsg.db, composite PK (session_uuid, partition) (Phase 2)
_SESSION_TAGS_TABLE = "session_tags"               # sessions.db, uuid-keyed (Phase 4)

# Store names, defined once here — both build_report() and prune() key their
# per-store results by these exact strings, so a typo in one can never
# silently desync it from the other the way two independent literal-string
# call sites could.
STORE_SCHEDULER_RECONCILE = "scheduler-reconcile-markers"
STORE_SCHEDULER_CURSORS = "scheduler-cursors"
STORE_MESSAGES_CURSORS = "messages-cursors"
STORE_SESSION_ENV = "session-env"
STORE_SESSIONS_INDEX = "sessions-index"
STORE_NAMES: tuple[str, ...] = (
    STORE_SCHEDULER_RECONCILE,
    STORE_SCHEDULER_CURSORS,
    STORE_MESSAGES_CURSORS,
    STORE_SESSION_ENV,
    STORE_SESSIONS_INDEX,
)


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
            STORE_SCHEDULER_RECONCILE,
            _scheduler_reconcile_uuids_db(scheduler_dir / "ccsched.db"),
            known,
        ),
        _store_report(
            STORE_SCHEDULER_CURSORS,
            _scheduler_cursor_uuids_db(scheduler_dir / "ccsched.db"),
            known,
        ),
        _store_report(
            STORE_MESSAGES_CURSORS,
            _messages_cursor_uuids_db(messages_root / "ccmsg.db"),
            known,
        ),
        _store_report(
            STORE_SESSION_ENV,
            _session_env_uuids(session_env_dir),
            known,
        ),
        _store_report(
            STORE_SESSIONS_INDEX,
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
    if report.total_orphaned:
        lines.append(f"Run `ccst gc prune` to remove the {report.total_orphaned} orphaned entries.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# prune (ccst gc prune) — the execute half. Reuses build_report()'s orphan
# definition unchanged; adds a per-uuid minimum-age floor and, with
# --execute, the actual deletes.
# ---------------------------------------------------------------------------


def _scheduler_reconcile_timestamps_db(ccsched_db_path: Path) -> dict[str, str]:
    """Session uuids with a reconcile-throttle row, mapped to their raw
    ``last_reconciled_at`` string — one of the 3 activity-recency signals fed
    into ``_uuid_age_hours``."""
    if not ccsched_db_path.exists():
        return {}
    conn = _db.connect(ccsched_db_path, readonly=True)
    try:
        rows = conn.execute(
            f"SELECT session_uuid, last_reconciled_at FROM {_SCHEDULER_RECONCILE_TABLE}"
        ).fetchall()
    finally:
        conn.close()
    return {row["session_uuid"]: row["last_reconciled_at"] for row in rows}


def _session_tags_timestamps_db(sessions_db_path: Path) -> dict[str, str]:
    """Session uuids with a session_tags row, mapped to their raw
    ``updated_at`` string — the 2nd of the 3 activity-recency signals."""
    if not sessions_db_path.exists():
        return {}
    conn = _db.connect(sessions_db_path, readonly=True)
    try:
        rows = conn.execute(f"SELECT uuid, updated_at FROM {_SESSION_TAGS_TABLE}").fetchall()
    finally:
        conn.close()
    return {row["uuid"]: row["updated_at"] for row in rows}


def _session_env_mtimes(session_env_dir: Path) -> dict[str, float]:
    """Session uuids with a session-env directory, mapped to its mtime (epoch
    seconds) — the 3rd of the 3 activity-recency signals."""
    out: dict[str, float] = {}
    if not session_env_dir.is_dir():
        return out
    for p in session_env_dir.iterdir():
        if p.is_dir():
            out[p.name] = p.stat().st_mtime
    return out


def _uuid_age_hours(
    reconcile_ts: dict[str, str],
    session_tags_ts: dict[str, str],
    session_env_mtimes: dict[str, float],
    now: datetime,
) -> dict[str, float]:
    """Merge the 3 timestamped-store signals into one ``uuid -> age in hours``
    map, taking the *most recent* signal per uuid where more than one source
    has an entry — the conservative reading, since it's the one least likely
    to under-count how recently active a session actually was.

    ``scheduler-cursors`` (ccsched.db) and ``messages-cursors`` (ccmsg.db)
    have no timestamp column of their own — confirmed against their DDL in
    ``lib/scheduler/store.py`` and ``lib/messaging/repository.py`` — so
    ``prune()`` applies this same merged map to their orphaned uuids too. A
    uuid absent from this map (present in none of the 3 timestamped stores)
    has no age evidence anywhere; ``prune()`` treats that as unresolvable and
    never deletes it, regardless of ``min_age_hours``.
    """
    latest: dict[str, datetime] = {}
    for uuid, raw in reconcile_ts.items():
        ts = _parse_ts_or_none(raw)
        if ts is not None:
            latest[uuid] = max(latest.get(uuid, ts), ts)
    for uuid, raw in session_tags_ts.items():
        ts = _parse_ts_or_none(raw)
        if ts is not None:
            latest[uuid] = max(latest.get(uuid, ts), ts)
    for uuid, mtime in session_env_mtimes.items():
        ts = datetime.fromtimestamp(mtime, tz=timezone.utc)
        latest[uuid] = max(latest.get(uuid, ts), ts)
    return {uuid: (now - ts).total_seconds() / 3600.0 for uuid, ts in latest.items()}


def _delete_scheduler_reconcile(ccsched_db_path: Path, uuid: str) -> None:
    conn = _db.connect(ccsched_db_path)
    try:
        conn.execute(f"DELETE FROM {_SCHEDULER_RECONCILE_TABLE} WHERE session_uuid = ?", (uuid,))
        conn.commit()
    finally:
        conn.close()


def _delete_scheduler_cursor(ccsched_db_path: Path, uuid: str) -> None:
    conn = _db.connect(ccsched_db_path)
    try:
        conn.execute(f"DELETE FROM {_SCHEDULER_CURSORS_TABLE} WHERE session_uuid = ?", (uuid,))
        conn.commit()
    finally:
        conn.close()


def _delete_messages_cursor(ccmsg_db_path: Path, uuid: str) -> None:
    conn = _db.connect(ccmsg_db_path)
    try:
        conn.execute(f"DELETE FROM {_MESSAGES_CURSOR_TABLE} WHERE session_uuid = ?", (uuid,))
        conn.commit()
    finally:
        conn.close()


def _delete_session_tags(sessions_db_path: Path, uuid: str) -> None:
    conn = _db.connect(sessions_db_path)
    try:
        conn.execute(f"DELETE FROM {_SESSION_TAGS_TABLE} WHERE uuid = ?", (uuid,))
        conn.commit()
    finally:
        conn.close()


def _delete_session_env(session_env_dir: Path, uuid: str) -> None:
    """Filesystem delete — the only non-SQL store prune() touches, and the
    only one holding harness-owned data rather than one of this repo's own
    stores. Highest-scrutiny path in this module; keep it this narrow (one
    directory, one uuid, no globbing) if it's ever touched again."""
    shutil.rmtree(session_env_dir / uuid)


@dataclass(frozen=True, slots=True)
class StorePruneResult:
    """One store's prune outcome. ``deleted`` means "would delete" in a
    dry run (``PruneReport.executed`` is False) and "actually deleted" once
    ``--execute`` is passed — the two never coexist in one report, so the
    single field is unambiguous given ``executed``."""

    name: str
    deleted: int
    skipped_too_young: int
    skipped_age_unknown: int
    failed: int


@dataclass(frozen=True, slots=True)
class PruneReport:
    stores: tuple[StorePruneResult, ...]
    executed: bool

    @property
    def any_failed(self) -> bool:
        return any(s.failed for s in self.stores)


def _run_store_prune(
    *,
    name: str,
    orphaned_uuids: tuple[str, ...],
    age_hours: dict[str, float],
    min_age_hours: float,
    execute: bool,
    delete_one: Callable[[str], None],
) -> StorePruneResult:
    deleted = skipped_too_young = skipped_age_unknown = failed = 0
    for uuid in orphaned_uuids:
        age = age_hours.get(uuid)
        if age is None:
            skipped_age_unknown += 1
            continue
        if age < min_age_hours:
            skipped_too_young += 1
            continue
        if not execute:
            deleted += 1
            continue
        try:
            delete_one(uuid)
            deleted += 1
        except Exception as exc:  # noqa: BLE001 - boundary: isolate this uuid's
            # failure so the rest of this store, and every other store, still
            # completes; failure is never silent - counted here and reported
            # to the caller via StorePruneResult.failed / PruneReport.any_failed.
            print(f"ccst gc prune: {name}: failed to delete {uuid}: {exc}", file=sys.stderr)
            failed += 1
    return StorePruneResult(
        name=name,
        deleted=deleted,
        skipped_too_young=skipped_too_young,
        skipped_age_unknown=skipped_age_unknown,
        failed=failed,
    )


def prune(
    *,
    min_age_hours: float = DEFAULT_MIN_AGE_HOURS,
    execute: bool = False,
    only: frozenset[str] | None = None,
    projects_dir: Path | None = None,
    scheduler_dir: Path | None = None,
    messages_root: Path | None = None,
    session_env_dir: Path | None = None,
    sessions_dir: Path | None = None,
) -> PruneReport:
    """Delete (``execute=True``) or dry-run-report (``execute=False``) the
    orphaned entries ``build_report`` identifies, gated by a per-uuid
    minimum-age floor (``_uuid_age_hours``). Never invents its own orphan
    definition — ``build_report`` is the sole read path, unchanged.

    ``only``, when given, restricts processing to that subset of
    ``STORE_NAMES``.
    """
    projects_dir = projects_dir if projects_dir is not None else DEFAULT_PROJECTS_DIR
    scheduler_dir = scheduler_dir if scheduler_dir is not None else _default_scheduler_dir()
    messages_root = messages_root if messages_root is not None else _default_messages_root()
    session_env_dir = (
        session_env_dir if session_env_dir is not None else DEFAULT_SESSION_ENV_DIR
    )
    sessions_dir = sessions_dir if sessions_dir is not None else _default_sessions_db_path().parent

    report = build_report(
        projects_dir=projects_dir,
        scheduler_dir=scheduler_dir,
        messages_root=messages_root,
        session_env_dir=session_env_dir,
        sessions_dir=sessions_dir,
    )
    orphans_by_store = {s.name: s.orphaned_uuids for s in report.stores}

    ccsched_db_path = scheduler_dir / "ccsched.db"
    ccmsg_db_path = messages_root / "ccmsg.db"
    sessions_db_path = sessions_dir / "sessions.db"

    age_hours = _uuid_age_hours(
        _scheduler_reconcile_timestamps_db(ccsched_db_path),
        _session_tags_timestamps_db(sessions_db_path),
        _session_env_mtimes(session_env_dir),
        datetime.now(timezone.utc),
    )

    delete_fns: dict[str, Callable[[str], None]] = {
        STORE_SCHEDULER_RECONCILE: lambda uuid: _delete_scheduler_reconcile(ccsched_db_path, uuid),
        STORE_SCHEDULER_CURSORS: lambda uuid: _delete_scheduler_cursor(ccsched_db_path, uuid),
        STORE_MESSAGES_CURSORS: lambda uuid: _delete_messages_cursor(ccmsg_db_path, uuid),
        STORE_SESSION_ENV: lambda uuid: _delete_session_env(session_env_dir, uuid),
        STORE_SESSIONS_INDEX: lambda uuid: _delete_session_tags(sessions_db_path, uuid),
    }

    results = [
        _run_store_prune(
            name=name,
            orphaned_uuids=orphans_by_store[name],
            age_hours=age_hours,
            min_age_hours=min_age_hours,
            execute=execute,
            delete_one=delete_fns[name],
        )
        for name in STORE_NAMES
        if only is None or name in only
    ]
    return PruneReport(stores=tuple(results), executed=execute)


def format_prune_report(report: PruneReport) -> str:
    """Render a ``PruneReport`` as a fixed-width table, e.g. for ``ccst gc prune``."""
    if not report.stores:
        return "No stores selected (see --only)."

    lines: list[str] = []
    name_w = max(len(s.name) for s in report.stores)

    if report.executed:
        header = (
            f"{'Store':<{name_w}}", f"{'Deleted':>7}", f"{'Too young':>9}",
            f"{'Age-unknown':>11}", f"{'Failed':>6}",
        )
        lines.append("  ".join(header))
        lines.append("  ".join(("-" * name_w, "-" * 7, "-" * 9, "-" * 11, "-" * 6)))
        for s in report.stores:
            lines.append(
                f"{s.name:<{name_w}}  {s.deleted:>7}  {s.skipped_too_young:>9}  "
                f"{s.skipped_age_unknown:>11}  {s.failed:>6}"
            )
        total_deleted = sum(s.deleted for s in report.stores)
        total_too_young = sum(s.skipped_too_young for s in report.stores)
        total_unknown = sum(s.skipped_age_unknown for s in report.stores)
        total_failed = sum(s.failed for s in report.stores)
        lines.append("")
        lines.append(
            f"Deleted {total_deleted}, skipped {total_too_young} too young / "
            f"{total_unknown} age-unknown, failed {total_failed}."
        )
        if total_failed:
            lines.append("One or more deletions failed — see stderr above for detail.")
    else:
        dry_run_header = (
            f"{'Store':<{name_w}}", f"{'Prunable':>8}", f"{'Too young':>9}", f"{'Age-unknown':>11}",
        )
        lines.append("  ".join(dry_run_header))
        lines.append("  ".join(("-" * name_w, "-" * 8, "-" * 9, "-" * 11)))
        for s in report.stores:
            lines.append(
                f"{s.name:<{name_w}}  {s.deleted:>8}  {s.skipped_too_young:>9}  {s.skipped_age_unknown:>11}"
            )
        total_prunable = sum(s.deleted for s in report.stores)
        total_too_young = sum(s.skipped_too_young for s in report.stores)
        total_unknown = sum(s.skipped_age_unknown for s in report.stores)
        lines.append("")
        lines.append(
            f"Run with --execute to delete {total_prunable} prunable entries "
            f"({total_too_young} too young, {total_unknown} age-unknown — both left for next run)."
        )
    return "\n".join(lines)
