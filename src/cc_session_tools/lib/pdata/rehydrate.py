"""Applies the spec's rehydrate comparison/swap for one project (spec: "Triggers", "Process
safety"). Used by SessionStart, the hourly cron job, and `ccst pdata rehydrate`."""
from __future__ import annotations

import enum
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib.pdata import (
    dump,
    formats,
    repository,
    store,
    sync_lock,
    vector_clock,
    vector_clock_store,
)


class RehydrateOutcome(enum.Enum):
    FAST_FORWARDED = "fast_forwarded"
    NO_OP = "no_op"  # local already at or ahead of the dump
    FORK = "fork"  # surfaced, nothing written
    CHECKSUM_INVALID = "checksum_invalid"  # surfaced, nothing written
    DEFERRED = "deferred"  # another writer holds the lock right now — retry later
    # The database or the published dump is not the current format: nothing loaded, not a fork.
    MIGRATION_PENDING = "migration_pending"


@dataclass(frozen=True, slots=True)
class RehydrateResult:
    outcome: RehydrateOutcome
    from_machine: str | None = None
    # The adopted dump's OWN embedded dumped_at (dump.DumpInfo.dumped_at), not latest.sql's
    # filesystem mtime - mtime reflects when this machine's copy last changed on disk (the
    # local download/OneDrive-sync-settle time for a synced file), not when the source machine
    # actually published it, which is the only thing worth showing a human. Set on
    # FAST_FORWARDED; None on every other outcome, since nothing was adopted.
    dumped_at: int | None = None
    # Set on MIGRATION_PENDING only: the local database's format and the published dump's
    # (None when nothing is published).
    local_format: int | None = None
    dump_format: int | None = None


def migration_pending_detail(result: RehydrateResult, *, project: str) -> str:
    """What to tell the user about a MIGRATION_PENDING outcome: both formats and the command.
    Raises on any other outcome, like conflict_detail."""
    if result.outcome is not RehydrateOutcome.MIGRATION_PENDING or result.local_format is None:
        raise ValueError(f"not a migration-pending outcome: {result.outcome.value}")
    return formats.migration_pending_detail(
        project, local_format=result.local_format, dump_format=result.dump_format,
    )


def format_gate(
    project: str, *, project_root: Path, existing: dump.DumpInfo,
) -> RehydrateResult | None:
    """A MIGRATION_PENDING result when `project` cannot sync until it is migrated, else None.
    That is: the local database is not the current format (it is never loaded into or
    published from, whatever is published), or `existing` - the published current-format dump -
    is a trustworthy dump of another format. Reads the database file read-only; a project with
    no database yet is the current format, since connecting creates it so."""
    local_format = formats.database_format(store.db_path(project))
    if local_format is not None and local_format != formats.CURRENT_PDATA_FORMAT:
        return RehydrateResult(
            outcome=RehydrateOutcome.MIGRATION_PENDING,
            local_format=local_format,
            dump_format=_published_format(project_root, existing=existing),
        )
    mismatched = dump.mismatched_dump_format(
        existing, local_format=formats.CURRENT_PDATA_FORMAT,
    )
    if mismatched is not None:
        return RehydrateResult(
            outcome=RehydrateOutcome.MIGRATION_PENDING,
            local_format=formats.CURRENT_PDATA_FORMAT,
            dump_format=mismatched,
        )
    return None


def _published_format(project_root: Path, *, existing: dump.DumpInfo) -> int | None:
    """The format of the newest dump published for the project: the current-format location
    first (by its header, or by its location when the dump is untrustworthy), then the format 3
    location; None when neither holds a dump."""
    if existing.present:
        if existing.pdata_format is None:
            return formats.CURRENT_PDATA_FORMAT
        return existing.pdata_format
    legacy = dump.latest_path(project_root, pdata_format=formats.PDATA_FORMAT_INTEGER_IDS)
    return formats.PDATA_FORMAT_INTEGER_IDS if legacy.exists() else None


def conflict_detail(result: RehydrateResult, *, project: str) -> str:
    """What to tell the user about a FORK or CHECKSUM_INVALID rehydrate outcome: what happened,
    and which command fixes it.

    Shared by `ccst pdata rehydrate`'s stderr line, its sync_notify.notify_conflict() detail, and
    the SessionStart hook's own notification + systemMessage. Both audiences want the same pair
    (what happened + the remedy command), and the remedy commands are identical, so a second copy
    of the wording would be pure drift risk.

    Raises on any other outcome rather than inventing a message for it: NO_OP/DEFERRED/
    FAST_FORWARDED are not conflicts, and a caller asking for a conflict message about one is a
    bug that must surface here rather than reach a Telegram push."""
    if result.outcome is RehydrateOutcome.FORK:
        return (
            f"unresolved fork with {result.from_machine} - run `ccst pdata resolve "
            f"--project {project}`"
        )
    if result.outcome is RehydrateOutcome.CHECKSUM_INVALID:
        return (
            "published dump fails its checksum check - run `ccst pdata dump --force` "
            "on the machine with good data to republish"
        )
    raise ValueError(f"not a conflict outcome: {result.outcome.value}")


def rehydrate(project: str, *, force: bool = False) -> RehydrateResult:
    project_root = store.project_root(project)
    info = dump.read_latest(project_root)
    # Before anything else, and not overridable by force: a format mismatch must never replace
    # the local database (design D5).
    pending = format_gate(project, project_root=project_root, existing=info)
    if pending is not None:
        return pending
    if not info.checksum_valid:
        return RehydrateResult(outcome=RehydrateOutcome.CHECKSUM_INVALID)

    conn = repository.connect(project)
    try:
        # A bare, non-transactional SELECT is still safe here even though isolation_level=None
        # means no implicit BEGIN precedes it: that setting only controls whether *Python's*
        # sqlite3 module opens a transaction for us, not whether SQLite itself does. SQLite runs
        # every statement — including a single autocommit SELECT — inside its own implicit read
        # transaction for that statement's duration, and WAL mode gives every reader a
        # consistent snapshot for that whole duration. So a concurrent local writer's commit is
        # seen either fully or not at all: this read can come back *stale* (superseded a moment
        # later by a write already in flight, which the lock check below can't catch either —
        # see rehydrate.py's module docstring reference to "Process safety"'s one-instant check),
        # but never *torn*/half-written. Confirmed against repository.py (isolation_level=None,
        # no explicit BEGIN wrapping this call) and db.py (WAL mode is enabled unconditionally
        # on every connection) — this is not an assumption.
        local_vector = vector_clock_store.read_vector(conn)
    finally:
        conn.close()

    comparison = vector_clock.compare(local=local_vector, dump=info.vector)
    if comparison is vector_clock.Comparison.LOCAL_DOMINATES and not force:
        return RehydrateResult(outcome=RehydrateOutcome.NO_OP)
    if comparison is vector_clock.Comparison.FORK and not force:
        return RehydrateResult(outcome=RehydrateOutcome.FORK, from_machine=info.machine_id)

    db_path = store.db_path(project)
    if sync_lock.is_locked(db_path):
        return RehydrateResult(outcome=RehydrateOutcome.DEFERRED)

    latest = dump.latest_path(project_root)
    tmp_path = build_replacement(db_path.parent, dump.sql_body(latest.read_text()))
    try:
        tmp_path.replace(db_path)  # atomic directory-entry swap on the same filesystem
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    drop_stale_wal_sidecars(db_path)

    return RehydrateResult(
        outcome=RehydrateOutcome.FAST_FORWARDED,
        from_machine=info.machine_id,
        dumped_at=info.dumped_at,
    )


def build_replacement(db_dir: Path, sql_body: str) -> Path:
    """Write sql_body into a brand-new sqlite file next to the live .db, ready for an atomic
    swap. Deliberately NOT opened through db.connect() — no WAL mode, no `PRAGMA
    foreign_keys=ON` — for two independent reasons:

    - dump.serialize() orders its CREATE/INSERT statements by table *name*, not by foreign-key
      dependency (e.g. "ext_g" sorts before "records" alphabetically), so an ext_<group> table's
      rows can be INSERTed before the records rows they reference. That is exactly what a real
      dump looks like whenever a project has any extension table — turning foreign_keys ON
      during this one-shot replay would make that perfectly valid dump fail to load.
    - WAL mode is a no-op to skip here: it's a per-connection PRAGMA (db.connect() re-applies it
      on every open, it is not carried by a one-off sqlite3.connect()), and any future caller
      that reopens the resulting file through repository.connect() re-enables it itself. Nothing
      about the live store's invariants depends on this temporary connection seeing either
      pragma.
    """
    with tempfile.NamedTemporaryFile(dir=db_dir, suffix=".db", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        tmp_conn = sqlite3.connect(tmp_path)
        try:
            tmp_conn.executescript(sql_body)
        finally:
            tmp_conn.close()
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def drop_stale_wal_sidecars(db_path: Path) -> None:
    """tmp_path.replace(db_path) only swaps db_path's directory entry for a new inode — rename(2)
    is atomic on a POSIX filesystem (Linux/WSL2 included) regardless of what else has either path
    open, so that part of the old "atomic on the same filesystem" reasoning holds. What it does
    NOT do is say anything about the WAL-mode sidecars every live pdata .db has: db.connect()
    unconditionally puts every connection into WAL mode, so db_path normally has `<db_path>-wal`
    and `<db_path>-shm` files sitting next to it. Those sidecars are named from db_path's *path*,
    not the inode being replaced, so the swap leaves them completely untouched — any future
    connection would pair the brand-new main file with a -wal built for the file it just
    replaced, a mismatch SQLite has no obligation to recover from cleanly. Closing our own `conn`
    above, while still pointed at the pre-swap file, usually triggers SQLite's own
    last-connection-close checkpoint (which deletes these as a side effect) — but only when ours
    really was the *last* open connection to that file, and nothing here can detect whether some
    other reader (a concurrent `ccst pdata` read, say) is still attached to it. So the cleanup is
    done explicitly and unconditionally rather than trusted to have already happened.

    Best-effort, deliberately: the swap itself has already succeeded by the time this runs, and
    this whole design is non-blocking/best-effort throughout (spec: "Process safety") - a sidecar
    that can't be unlinked (e.g. a permission error, or another process genuinely holding it open)
    must not turn an otherwise-successful rehydrate into an unhandled exception. `missing_ok=True`
    only swallows "it's already gone"; any other OSError is caught per-file too, so one failing
    sidecar doesn't stop the other from being cleaned up."""
    for suffix in ("-wal", "-shm"):
        try:
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
        except OSError:
            pass
