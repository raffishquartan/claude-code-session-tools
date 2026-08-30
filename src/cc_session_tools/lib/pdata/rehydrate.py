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


@dataclass(frozen=True, slots=True)
class RehydrateResult:
    outcome: RehydrateOutcome
    from_machine: str | None = None


def rehydrate(project: str, *, force: bool = False) -> RehydrateResult:
    project_root = store.project_root(project)
    info = dump.read_latest(project_root)
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

    latest = project_root / ".pdata-db-dump" / "latest.sql"
    sql_body = _sql_body(latest.read_text())
    tmp_path = _build_replacement(db_path.parent, sql_body)
    try:
        tmp_path.replace(db_path)  # atomic directory-entry swap on the same filesystem
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    _drop_stale_wal_sidecars(db_path)

    return RehydrateResult(outcome=RehydrateOutcome.FAST_FORWARDED, from_machine=info.machine_id)


def _build_replacement(db_dir: Path, sql_body: str) -> Path:
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


def _drop_stale_wal_sidecars(db_path: Path) -> None:
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
    done explicitly and unconditionally rather than trusted to have already happened."""
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)


def _sql_body(text: str) -> str:
    """Strip dump.write_latest()'s header block (machine_id/dumped_at/vector comment lines),
    leaving just the executable SQL. Finds the header/body boundary by locating the literal
    "BEGIN TRANSACTION;" line serialize() always emits first, rather than filtering every line
    that happens to start with "--" — a real bug a code review caught in dump.py's own
    read_latest() and which this function would otherwise share: a records.content value is
    free-text project data and can itself contain a line starting with "--" (a pasted code
    snippet, say), which a blanket "--"-line filter would silently corrupt or truncate. Taking
    the *first* such line is still correct even if some row's content also contains that exact
    line later in the file: serialize() emits "BEGIN TRANSACTION;" as the very first line of the
    body, before any row data exists to collide with it, so nothing earlier in the file can ever
    produce a false match."""
    lines = text.splitlines()
    start = lines.index("BEGIN TRANSACTION;")
    return "\n".join(lines[start:])
