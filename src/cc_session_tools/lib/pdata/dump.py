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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from cc_session_tools.lib.pdata import formats, vector_clock, vector_clock_store

_ARCHIVE_KEEP = 24

# The project-relative directory every pdata dump lives under. Format 3 (ccst 3.x) dumps sit
# directly in it; each later format publishes to its own subdirectory, so a ccst release that
# does not understand a format never finds that format's dump where it looks (design D5).
DUMP_ROOT_DIR_NAME: Final = ".pdata-db-dump"
_FORMAT_SUBDIR: Final[Mapping[int, str | None]] = {
    formats.PDATA_FORMAT_INTEGER_IDS: None,
    formats.PDATA_FORMAT_UUID_IDS: "v4",
}

# Header line prefixes. A dump with no `-- pdata_format=` line is format 3: ccst 3.x wrote none.
_FORMAT_PREFIX: Final = "-- pdata_format="
_MACHINE_PREFIX: Final = "-- machine_id="
_DUMPED_AT_PREFIX: Final = "-- dumped_at="
_VECTOR_PREFIX: Final = "-- vector:"
# The format 3 vector clock a migrated database had when `ccst pdata migrate-uuid` converted
# it, so a machine migrating later can check its own format 3 state is the same lineage.
_MIGRATED_FROM_PREFIX: Final = "-- migrated_from_format3_vector:"

# serialize()'s output always starts with this exact line - the single source of truth for where
# write_latest()'s header block (the `-- ` comment lines) ends and the real SQL
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
    # Is there a latest.sql on disk at all? `checksum_valid` alone conflates two states that a
    # human-driven command can reasonably treat alike but an unattended, repeating one cannot:
    # "this project has never been dumped" (the normal state of every project between `ccst pdata
    # init` and its first publish) and "a dump exists but fails its checksum" (real corruption,
    # or a publish interrupted between latest.sql and latest.sha256). The hourly sync-check job
    # publishes the first dump in the former case and reports a conflict in the latter; without
    # this field it would push a "published dump fails its checksum check" notification every
    # hour, forever, for every not-yet-dumped project.
    present: bool = False
    # The dump's format from its `-- pdata_format=` header line (format 3 when the line is
    # absent). None when the dump is absent or fails its checksum, since nothing in an
    # untrusted file is read.
    pdata_format: int | None = None
    # The `-- migrated_from_format3_vector:` lines, or None when the dump has none (it was not
    # published by a migration).
    migrated_from_format3_vector: dict[str, int] | None = None


def dump_dir(
    project_root: Path, *, pdata_format: int = formats.CURRENT_PDATA_FORMAT,
) -> Path:
    """Where dumps of `pdata_format` live for the project at `project_root`."""
    subdir = _FORMAT_SUBDIR[pdata_format]
    root = project_root / DUMP_ROOT_DIR_NAME
    return root if subdir is None else root / subdir


def latest_path(
    project_root: Path, *, pdata_format: int = formats.CURRENT_PDATA_FORMAT,
) -> Path:
    """The published `latest.sql` of `pdata_format` for the project at `project_root`."""
    return dump_dir(project_root, pdata_format=pdata_format) / "latest.sql"


def mismatched_dump_format(existing: DumpInfo, *, local_format: int) -> int | None:
    """The published dump's format when it is a trustworthy dump of a format other than
    `local_format`, else None. A mismatched dump is never loaded or published over: the
    project is waiting for `ccst pdata migrate-uuid`."""
    if existing.checksum_valid and existing.pdata_format != local_format:
        return existing.pdata_format
    return None


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
    """Publish `conn`'s database as the project's current-format `latest.sql`, archiving the
    previous one. Only a current-format database is ever published: format 3 dumps belong to
    ccst 3.x and this code never writes them. The database's recorded migration origin
    (vector_clock_store.read_migration_origin), when it has one, goes into the header on every
    publish (see `_MIGRATED_FROM_PREFIX`)."""
    local_format = formats.pdata_format(conn)
    if local_format != formats.CURRENT_PDATA_FORMAT:
        raise ValueError(
            f"refusing to publish a format {local_format} database: this ccst only publishes "
            f"format {formats.CURRENT_PDATA_FORMAT} dumps"
        )
    text = serialize(conn)
    header_lines = [
        f"{_FORMAT_PREFIX}{local_format}",
        f"{_MACHINE_PREFIX}{machine_id}",
        f"{_DUMPED_AT_PREFIX}{int(time.time())}",
        *(f"{_VECTOR_PREFIX}{k}={v}" for k, v in sorted(vector.items())),
    ]
    header_lines.extend(
        f"{_MIGRATED_FROM_PREFIX}{k}={v}"
        for k, v in sorted(vector_clock_store.read_migration_origin(conn).items())
    )
    full_text = "\n".join(header_lines) + "\n" + text
    checksum = hashlib.sha256(full_text.encode()).hexdigest()

    directory = dump_dir(project_root, pdata_format=local_format)
    archive_dir = directory / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    latest = directory / "latest.sql"
    if latest.exists():
        shutil.copy2(latest, _unique_archive_path(archive_dir))
        _prune_archive(archive_dir)

    # Deliberate order, not an oversight: latest.sql before latest.sha256. A crash or an
    # interrupted OneDrive sync between these two writes leaves a latest.sql with no matching
    # (or a stale) checksum file - read_latest() then correctly reports checksum_valid=False
    # rather than trusting a half-written dump.
    latest.write_text(full_text)
    (directory / "latest.sha256").write_text(checksum)


def _prune_archive(archive_dir: Path) -> None:
    files = sorted(archive_dir.glob("*.sql"))
    for stale in files[:-_ARCHIVE_KEEP] if len(files) > _ARCHIVE_KEEP else []:
        stale.unlink()


def read_latest(
    project_root: Path, *, pdata_format: int = formats.CURRENT_PDATA_FORMAT,
) -> DumpInfo:
    """The published dump at `pdata_format`'s location. Raises UnknownPdataFormatError when a
    trustworthy dump's `-- pdata_format=` line names a format no ccst release writes."""
    directory = dump_dir(project_root, pdata_format=pdata_format)
    latest = directory / "latest.sql"
    checksum_file = directory / "latest.sha256"
    if not latest.exists() or not checksum_file.exists():
        # `present` keys off latest.sql alone, not the pair: a latest.sql with a missing
        # latest.sha256 is exactly write_latest()'s documented interrupted-publish case - a dump
        # that exists and cannot be trusted, not a project that has never published one.
        return DumpInfo(
            checksum_valid=False, machine_id=None, vector={}, present=latest.exists(),
        )
    text = latest.read_text()
    actual = hashlib.sha256(text.encode()).hexdigest()
    expected = checksum_file.read_text().strip()
    if actual != expected:
        return DumpInfo(checksum_valid=False, machine_id=None, vector={}, present=True)
    machine_id = None
    dumped_at = None
    dump_format = formats.PDATA_FORMAT_INTEGER_IDS
    vector: dict[str, int] = {}
    migrated_from: dict[str, int] | None = None
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
        if line.startswith(_FORMAT_PREFIX):
            dump_format = _parse_format(line, latest)
        elif line.startswith(_MACHINE_PREFIX):
            machine_id = line.removeprefix(_MACHINE_PREFIX)
        elif line.startswith(_DUMPED_AT_PREFIX):
            dumped_at = int(line.removeprefix(_DUMPED_AT_PREFIX))
        elif line.startswith(_VECTOR_PREFIX):
            k, _, v = line.removeprefix(_VECTOR_PREFIX).partition("=")
            vector[k] = int(v)
        elif line.startswith(_MIGRATED_FROM_PREFIX):
            k, _, v = line.removeprefix(_MIGRATED_FROM_PREFIX).partition("=")
            migrated_from = {} if migrated_from is None else migrated_from
            migrated_from[k] = int(v)
    return DumpInfo(
        checksum_valid=True, machine_id=machine_id, vector=vector, dumped_at=dumped_at,
        present=True, pdata_format=dump_format, migrated_from_format3_vector=migrated_from,
    )


def _parse_format(line: str, latest: Path) -> int:
    raw = line.removeprefix(_FORMAT_PREFIX)
    if raw.isdigit() and int(raw) in _FORMAT_SUBDIR:
        return int(raw)
    raise formats.UnknownPdataFormatError(
        f"{latest}: header line {line!r} names a dump format this ccst does not know "
        f"(known: {', '.join(str(f) for f in sorted(_FORMAT_SUBDIR))})"
    )


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


def is_no_op_publish(*, local_vector: dict[str, int], existing: DumpInfo) -> bool:
    """True iff `local_vector` exactly equals the currently-published dump's vector - a
    `decide_publish()`-safe write right now would produce byte-identical data and vector content,
    refreshing only the `machine_id`/`dumped_at` header fields.

    A separate, additive check rather than folded into `decide_publish()` itself: an *explicit*
    trigger (`ccst pdata dump`, or a session ending with `on_session_end`) may reasonably still
    want to republish even when unchanged - to refresh the header after fixing a checksum
    failure, say - so neither of those two existing callers is changed by this function's
    existence. An *automatic, unattended, repeating* trigger (the hourly `ccsched` job) is a
    different case: calling `write_latest()` every single cycle regardless of activity means a
    real archive-copy and a changed `latest.sql`/`latest.sha256` for every idle project, every
    hour, forever - real disk churn and (since `.pdata-db-dump/` lives under a synced folder)
    real OneDrive sync traffic for zero actual change. A caller in that position should check
    this first and skip `write_latest()` entirely when it's `True`, rather than call
    `decide_publish()` alone and treat every `None` result as "go ahead and write".

    `existing.checksum_valid` is checked explicitly (not delegated to `decide_publish()`) because
    a missing/invalid dump is never a no-op - there is nothing to be equal *to* yet, so it must
    always be treated as "worth publishing", matching `decide_publish()`'s own unconditional-safe
    branch for that case."""
    return existing.checksum_valid and local_vector == existing.vector


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
    """Strip write_latest()'s header block (the `-- ` comment lines) from a
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
