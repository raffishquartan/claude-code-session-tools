"""Correctness-keyed garbage-collection report for the four per-session-uuid
data stores that accumulate one file/directory per session forever, with no
existing cleanup code:

  ~/.claude/cc-scheduler/.reconcile.<uuid>.ts        (reconcile throttle marker)
  ~/.claude/cc-scheduler/.cursors/<uuid>.json         (scheduler digest cursor)
  ~/.claude/cc-messages/.cursors/<uuid>.json          (messaging delivery cursor)
  ~/.claude/session-env/<uuid>/                       (harness-created, not by
                                                        this repo, but same rule
                                                        applies)

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

from cc_session_tools.lib.messaging.store import store_root as _default_messages_root
from cc_session_tools.lib.scheduler.state import scheduler_dir as _default_scheduler_dir

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_SESSION_ENV_DIR = Path.home() / ".claude" / "session-env"

_RECONCILE_PREFIX = ".reconcile."
_RECONCILE_SUFFIX = ".ts"


@dataclass(frozen=True, slots=True)
class StoreReport:
    """Orphan count for one of the four uuid-keyed stores."""

    name: str
    total: int
    orphaned_uuids: tuple[str, ...]

    @property
    def orphaned(self) -> int:
        return len(self.orphaned_uuids)


@dataclass(frozen=True, slots=True)
class GcReport:
    """Full report across all four stores."""

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
    ``<projects_dir>/*/<uuid>.jsonl``."""
    if not projects_dir.is_dir():
        return set()
    return {p.stem for p in projects_dir.glob("*/*.jsonl")}


def _reconcile_marker_uuids(scheduler_dir: Path) -> dict[str, Path]:
    """``<scheduler_dir>/.reconcile.<uuid>.ts``"""
    out: dict[str, Path] = {}
    if not scheduler_dir.is_dir():
        return out
    for p in scheduler_dir.glob(f"{_RECONCILE_PREFIX}*{_RECONCILE_SUFFIX}"):
        if not p.is_file():
            continue
        uuid = p.name[len(_RECONCILE_PREFIX) : -len(_RECONCILE_SUFFIX)]
        out[uuid] = p
    return out


def _cursor_uuids(cursors_dir: Path) -> dict[str, Path]:
    """``<cursors_dir>/<uuid>.json`` — shared shape used by both the
    scheduler's and the messaging store's cursor directories."""
    out: dict[str, Path] = {}
    if not cursors_dir.is_dir():
        return out
    for p in cursors_dir.glob("*.json"):
        if p.is_file():
            out[p.stem] = p
    return out


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
) -> GcReport:
    """Enumerate known session uuids and all four stores, and compute the
    orphan set per store. Read-only: never deletes or modifies anything.

    Each directory can be overridden explicitly (used by tests and by the
    CLI's override flags); when omitted, each store resolves its own default
    the same way its owning module does (respecting that module's env-var
    override, e.g. ``CC_SCHEDULER_DIR`` / ``CCST_MESSAGES_ROOT``).
    """
    projects_dir = projects_dir if projects_dir is not None else DEFAULT_PROJECTS_DIR
    scheduler_dir = scheduler_dir if scheduler_dir is not None else _default_scheduler_dir()
    messages_root = messages_root if messages_root is not None else _default_messages_root()
    session_env_dir = (
        session_env_dir if session_env_dir is not None else DEFAULT_SESSION_ENV_DIR
    )

    known = known_session_uuids(projects_dir)

    stores = (
        _store_report(
            "scheduler-reconcile-markers",
            _reconcile_marker_uuids(scheduler_dir),
            known,
        ),
        _store_report(
            "scheduler-cursors",
            _cursor_uuids(scheduler_dir / ".cursors"),
            known,
        ),
        _store_report(
            "messages-cursors",
            _cursor_uuids(messages_root / ".cursors"),
            known,
        ),
        _store_report(
            "session-env",
            _session_env_uuids(session_env_dir),
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
