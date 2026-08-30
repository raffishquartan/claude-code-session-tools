"""Deterministic text serialisation of one project's .db (spec: "Dump format"). NOT
sqlite3.Connection.iterdump() used as-is — empirically confirmed during design that a table with
a composite/non-integer primary key (record_group_fields) dumps in insertion order under
iterdump(), not key order, making two logically-identical databases with different edit histories
produce different dump bytes. This module sorts tables/indices by name explicitly and adds an
explicit ORDER BY per table's own primary-key columns, rather than relying on iterdump()'s
unordered per-table fetch."""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cc_session_tools.lib.pdata import vector_clock

_ARCHIVE_KEEP = 24

# serialize()'s output always starts with this exact line - the single source of truth for where
# write_latest()'s header block (machine_id/dumped_at/vector comment lines) ends and the real SQL
# body begins. Used by both read_latest() below and sql_body() (called from rehydrate.py) -
# previously each independently re-derived the same boundary with its own copy of this string,
# which is exactly the kind of drift this repo's coding standards call out: one source of truth
# for a shared constant, not a second copy "just for now".
_BODY_SENTINEL = "BEGIN TRANSACTION;"


def _primary_key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    pk_rows = [r for r in rows if r["pk"] > 0]
    pk_rows.sort(key=lambda r: r["pk"])  # PRAGMA table_info's pk column is the PK's column *order*
    return [r["name"] for r in pk_rows]


def serialize(conn: sqlite3.Connection) -> str:
    lines = [_BODY_SENTINEL]
    schema_rows = conn.execute(
        "SELECT name, type, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND type IN ('table', 'index') AND name != 'sqlite_sequence' "
        "ORDER BY name"
    ).fetchall()
    for row in schema_rows:
        if row["type"] != "table":
            continue
        lines.append(row["sql"] + ";")
        pk_cols = _primary_key_columns(conn, row["name"])
        order_by = f" ORDER BY {', '.join(pk_cols)}" if pk_cols else ""
        data_rows = conn.execute(f'SELECT * FROM "{row["name"]}"{order_by}').fetchall()
        for data_row in data_rows:
            values = ", ".join(_sql_literal(v) for v in tuple(data_row))
            lines.append(f'INSERT INTO "{row["name"]}" VALUES({values});')
    for row in schema_rows:
        if row["type"] == "index":
            lines.append(row["sql"] + ";")
    lines.append("COMMIT;")
    return "\n".join(lines)


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


@dataclass(frozen=True, slots=True)
class DumpInfo:
    checksum_valid: bool
    machine_id: str | None
    vector: dict[str, int]
    dumped_at: int | None = None


def _dump_dir(project_root: Path) -> Path:
    return project_root / ".pdata-db-dump"


def _unique_archive_path(archive_dir: Path) -> Path:
    """A filename for this archived dump, unique within archive_dir, whose lexicographic sort
    order (what _prune_archive relies on to find "most recent") always matches real chronological
    order - including across files that were created, then deleted by pruning, then created again
    under the same wall-clock second.

    Keyed on nanosecond-resolution wall-clock time (time.time_ns(), zero-padded to a fixed width
    so lexicographic and numeric order agree) rather than whole seconds, because write_latest can
    run more than once within the same *second* - e.g. the archive-pruning test, which loops 30
    times with no sleep - and integer-second timestamps collide in that case. Nanosecond
    resolution makes a genuine collision between two real write_latest calls vanishingly unlikely
    (they're separated by real disk I/O), but the loop below still guards against one explicitly
    rather than assume it can't happen.

    Two earlier, rejected approaches, left here because each looked correct until tested against
    this exact archive-pruning scenario and wasn't:
    - A bare per-second timestamp with no disambiguation: silently overwrites on collision via
      shutil.copy2, under-filling the archive regardless of the retention count.
    - A bare, unpadded collision suffix appended only when needed ("-1", "-2", ...): avoids the
      overwrite, but "-10" sorts before "-2" lexicographically, and a suffixed name sorts before
      a bare one at all ("-" < "."), so pruning ended up removing some of the newest entries and
      keeping some of the oldest.
    - A zero-padded suffix that still started counting from 0 on every call: fixed the sort order,
      but once pruning deleted the lowest-numbered file, the *next* call's search-from-0 would
      reuse that now-free slot for brand-new content - silently resetting that slot back to
      "oldest" by name even though its content was the newest thing just written, so pruning
      would immediately delete it again on the very next write. Traced directly: from the 25th
      write_latest call onward, every new archive got written into the just-freed slot 0 and
      pruned right back out, so vectors from call 25 onward never actually survived into the
      archive at all. Nanosecond timestamps have no reusable "slot" to begin with."""
    while True:
        candidate = archive_dir / f"{time.time_ns():020d}.sql"
        if not candidate.exists():
            return candidate


def write_latest(
    conn: sqlite3.Connection, *, project_root: Path, machine_id: str, vector: dict[str, int],
) -> None:
    text = serialize(conn)
    header = f"-- machine_id={machine_id}\n-- dumped_at={int(time.time())}\n"
    header += "\n".join(f"-- vector:{k}={v}" for k, v in sorted(vector.items())) + "\n"
    full_text = header + text
    checksum = hashlib.sha256(full_text.encode()).hexdigest()

    dump_dir = _dump_dir(project_root)
    archive_dir = dump_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    latest = dump_dir / "latest.sql"
    if latest.exists():
        shutil.copy2(latest, _unique_archive_path(archive_dir))
        _prune_archive(archive_dir)

    # Deliberate order, not an oversight: latest.sql before latest.sha256. A crash or an
    # interrupted OneDrive sync between these two writes leaves a latest.sql with no matching
    # (or a stale) checksum file - read_latest() then correctly reports checksum_valid=False
    # rather than trusting a half-written dump.
    latest.write_text(full_text)
    (dump_dir / "latest.sha256").write_text(checksum)


def _prune_archive(archive_dir: Path) -> None:
    files = sorted(archive_dir.glob("*.sql"))
    for stale in files[:-_ARCHIVE_KEEP] if len(files) > _ARCHIVE_KEEP else []:
        stale.unlink()


def read_latest(project_root: Path) -> DumpInfo:
    dump_dir = _dump_dir(project_root)
    latest = dump_dir / "latest.sql"
    checksum_file = dump_dir / "latest.sha256"
    if not latest.exists() or not checksum_file.exists():
        return DumpInfo(checksum_valid=False, machine_id=None, vector={})
    text = latest.read_text()
    actual = hashlib.sha256(text.encode()).hexdigest()
    expected = checksum_file.read_text().strip()
    if actual != expected:
        return DumpInfo(checksum_valid=False, machine_id=None, vector={})
    machine_id = None
    dumped_at = None
    vector: dict[str, int] = {}
    for line in text.splitlines():
        # Stop at the header/body boundary - serialize()'s output always starts with this exact
        # line, so everything from here on is dumped row data, not metadata. Scanning the whole
        # file instead of stopping here is a real bug, not a theoretical one: a records.content
        # value is free-text project data and can itself contain a line that happens to start
        # with "-- vector:" or "-- machine_id=" (a pasted code snippet or transcript, say) -
        # mistaking that for real header metadata silently corrupts the vector this whole sync
        # design's fork/fast-forward decision depends on.
        if line == _BODY_SENTINEL:
            break
        if line.startswith("-- machine_id="):
            machine_id = line.removeprefix("-- machine_id=")
        elif line.startswith("-- dumped_at="):
            dumped_at = int(line.removeprefix("-- dumped_at="))
        elif line.startswith("-- vector:"):
            rest = line.removeprefix("-- vector:")
            k, _, v = rest.partition("=")
            vector[k] = int(v)
    return DumpInfo(checksum_valid=True, machine_id=machine_id, vector=vector, dumped_at=dumped_at)


def decide_publish(
    *, local_vector: dict[str, int], existing: DumpInfo,
) -> vector_clock.Comparison | None:
    """Is it safe to publish local's state over `existing` right now? `None` means yes; any
    returned `Comparison` is the reason it isn't.

    A missing/checksum-invalid dump (including the very first dump ever) is always safe to
    publish over unconditionally. Otherwise it's safe iff local strictly dominates (or equals)
    the published dump - compare()'s own "missing entries default to 0" rule already makes an
    empty/no-vector existing dump come out LOCAL_DOMINATES here, so there is no separate "dump
    has no vector at all" branch. FORK and DUMP_DOMINATES are both refused: a plain dump publish
    is not itself a local write (write_latest bumps no vector), so overwriting either would
    silently discard revisions the other side may still need.

    Shared by `ccst pdata dump` and the SessionEnd hook rather than computed inline in each -
    they implement the same spec rule ("Triggers", dump row) and must not drift into two
    subtly different publish decisions."""
    if not existing.checksum_valid:
        return None
    comparison = vector_clock.compare(local=local_vector, dump=existing.vector)
    if comparison is vector_clock.Comparison.LOCAL_DOMINATES:
        return None
    return comparison


def refusal_detail(project: str) -> str:
    """The one wording for "local diverges from the published dump, refusing to publish", shared
    by `ccst pdata dump`'s stderr line and the SessionEnd hook's conflict notification. Kept in
    one place so the CLI and the hook can never describe the same conflict differently."""
    return (
        f"refusing to publish - local diverges from the published dump (run "
        f"`ccst pdata resolve --project {project}` to resolve, or pass --force "
        f"to publish local as the winner anyway)"
    )


def format_dumped_at(dumped_at: int) -> str:
    """One `DumpInfo.dumped_at` rendered for a human. The single source of truth for this
    format string, called by init_service.py's adoption message and by the SessionStart hook's
    re-hydration message - a second literal copy of the strftime pattern is exactly the drift
    this repo's coding standards warn about."""
    return datetime.fromtimestamp(dumped_at).strftime("%Y-%m-%d %H:%M")


def sql_body(text: str) -> str:
    """Strip write_latest()'s header block (machine_id/dumped_at/vector comment lines) from a
    dump, leaving just the executable SQL - for rehydrate.py's executescript() call. Shares
    _BODY_SENTINEL with serialize()/read_latest() above rather than re-deriving the header/body
    boundary a second time (a prior version of this logic lived duplicated in rehydrate.py; a
    code review flagged the drift risk of two independent copies of the same boundary-finding
    rule). Taking the *first* occurrence of the sentinel is correct even if some row's content
    also contains that exact line later in the file: it's always the first line of the body,
    before any row data exists to collide with it."""
    lines = text.splitlines()
    start = lines.index(_BODY_SENTINEL)
    return "\n".join(lines[start:])
