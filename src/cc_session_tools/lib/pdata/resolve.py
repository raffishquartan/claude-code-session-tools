"""Cross-machine fork resolution for `ccst pdata resolve` (spec: "Conflict handling &
notification" — the relational-integrity paragraph and the "Post-resolve vector-clock update"
paragraph). Diffs the live local `.db` against the project's published dump, record-by-record
with each record's base row and extension row paired as one unit, and applies a caller-supplied
set of per-record "local"/"dump" choices as one atomic transaction — never a blunt whole-database
overwrite (that is rehydrate.py's job for the clean fast-forward case; this module is what
`ccst pdata resolve` reaches for once vector_clock.compare() has already reported a genuine FORK).

Never auto-merges, never silently keeps one side and discards the other — every record surfaced
here needs an explicit choice from the caller (ultimately Chris, via the CLI/skill), matching the
existing `pm-pdata-conflict-resolution` skill's single-file-conflict framing exactly.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib import machine_identity
from cc_session_tools.lib.pdata import (
    dump,
    naming,
    repository,
    store,
    vector_clock,
    vector_clock_store,
)

_VALID_CHOICES = frozenset({"local", "dump"})


@dataclass(frozen=True, slots=True)
class RecordDiff:
    """One resolvable unit: a `records` row (+ its `ext_<record_group>` row, if any) that differs
    between local and the dump, or exists on only one side. Base and extension are always paired
    here — never split into two separate diffs — per the spec's relational-integrity requirement.

    `local`/`dump` are each `None` if the record does not exist on that side, otherwise a dict of
    the shape `{"base": {...six records columns...}, "extension": {...columns...} | None}` —
    `extension` is `None` when the record's group has no ext_<group> table on that side at all,
    and a (possibly empty) dict of column->value when it does.
    """

    record_id: int
    record_group: str
    local: dict[str, object] | None
    dump: dict[str, object] | None
    is_delete_vs_update: bool
    id_collision: bool


@dataclass(frozen=True, slots=True)
class SchemaFieldDiff:
    """`record_group_fields` can diverge independently of any record — its own diff category."""

    record_group: str
    field_name: str
    present_locally: bool
    present_in_dump: bool


@dataclass(frozen=True, slots=True)
class ResolveDiff:
    records: list[RecordDiff]
    schema_fields: list[SchemaFieldDiff]
    dump_vector: dict[str, int]
    dump_machine_id: str | None


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """One side's view of one record_id — record_group kept separate from payload so id-collision
    detection (see `_classify`) can compare it without reaching into the payload dict."""

    record_group: str
    payload: dict[str, object]  # {"base": {...}, "extension": {...} | None}


def diff_against_dump(project: str) -> ResolveDiff:
    """Diff project's live local `.db` against its published dump. Raises ValueError if the dump
    fails its checksum — there is nothing reliable to diff against in that case (spec: "Checksum
    failure... nothing reliable to diff"; the fix is `ccst pdata dump --force`, not a resolve)."""
    project_root = store.project_root(project)
    info = dump.read_latest(project_root)
    if not info.checksum_valid:
        raise ValueError(
            f"dump for project {project!r} fails its checksum check — nothing reliable to diff "
            f"against (see `ccst pdata dump --force` to republish from local)"
        )

    dump_conn = _open_dump(project_root)
    try:
        local_conn = repository.connect(project)
        try:
            records = _diff_records(local_conn, dump_conn)
            schema_fields = _diff_schema_fields(local_conn, dump_conn)
        finally:
            local_conn.close()
    finally:
        dump_conn.close()

    return ResolveDiff(
        records=records,
        schema_fields=schema_fields,
        dump_vector=info.vector,
        dump_machine_id=info.machine_id,
    )


def apply_resolution(project: str, choices: dict[int, str]) -> None:
    """Apply choices (`{record_id: "local" | "dump"}`) for every RecordDiff the caller wants
    resolved — only record_ids appearing in choices are touched; anything not chosen is left
    as-is. One call is one transaction covering every chosen record plus the post-resolve
    vector-clock bookkeeping, followed by an immediate re-dump once that transaction commits
    (spec's exact three-step "Post-resolve vector-clock update", plus rehydrate.py's "always
    immediately re-dump" rule applied here for the identical reason)."""
    if not choices:
        raise ValueError("apply_resolution requires at least one record_id in choices")
    for record_id, choice in choices.items():
        if choice not in _VALID_CHOICES:
            raise ValueError(
                f"invalid choice {choice!r} for record_id {record_id}: must be 'local' or 'dump'"
            )

    project_root = store.project_root(project)
    diff = diff_against_dump(project)
    by_id = {record_diff.record_id: record_diff for record_diff in diff.records}

    missing = sorted(set(choices) - set(by_id))
    if missing:
        raise ValueError(f"record_id(s) {missing} are not part of the current diff")

    collisions = sorted(rid for rid in choices if by_id[rid].id_collision)
    if collisions:
        raise ValueError(
            f"record_id(s) {collisions} are an id collision — two unrelated records were "
            f"independently assigned the same id (see resolve.py's `_classify` docstring), not "
            f"a genuine edit conflict on one record. A local/dump choice would silently discard "
            f"one side's real, unrelated record, so apply_resolution refuses these; they need a "
            f"manual, out-of-band reconciliation, not a per-record local/dump pick"
        )

    dumpless = sorted(
        rid for rid, choice in choices.items() if choice == "dump" and by_id[rid].dump is None
    )
    if dumpless:
        raise ValueError(
            f"record_id(s) {dumpless}: choice 'dump' is invalid — the dump has no row for these "
            f"records (they exist locally only)"
        )

    dump_conn = _open_dump(project_root)
    try:
        local_conn = repository.connect(project)
        try:
            with repository._immediate(local_conn):
                for record_id in sorted(choices):
                    if choices[record_id] == "dump":
                        _apply_dump_choice(local_conn, dump_conn, by_id[record_id])
                    # choice == "local" needs no write: local's row is already what we're keeping.

                machine_id = machine_identity.resolve().machine_id
                local_vector = vector_clock_store.read_vector(local_conn)
                # Step 1 (spec): the resolve itself counts as exactly one local write, regardless
                # of how many records this call touched.
                vector_clock.bump_own(local_vector, machine_id)
                # Step 2 (spec): adopt the dump machine's revision as fully incorporated, max
                # every other machine — vector_clock.merge() is exactly that, applied on top of
                # the bump from step 1, not instead of it. See resolve.py's module-level note in
                # the implementation report on why this order and merge-then-bump are equivalent
                # under this system's own "a machine's live revision is never behind what any
                # remote dump believes about it" invariant.
                merged_vector = vector_clock.merge(local_vector, diff.dump_vector)
                vector_clock_store.write_vector(
                    local_conn, merged_vector, updated_at=int(time.time()),
                )
            # Step 3 (spec): re-dump immediately, after commit — a filesystem write, matching
            # rehydrate.rehydrate()'s own "always immediately re-dump" rule and for the identical
            # reason: publish the merged state right away so the other machine's next check sees
            # a dominating fast-forward, not a repeat of this fork.
            dump.write_latest(
                local_conn, project_root=project_root, machine_id=machine_id, vector=merged_vector,
            )
        finally:
            local_conn.close()
    finally:
        dump_conn.close()


def _open_dump(project_root: Path) -> sqlite3.Connection:
    """In-memory replay of the published dump. rehydrate.py's `_build_replacement()` builds the
    same kind of one-shot SQLite replica on disk, ready for an atomic swap; this module never
    swaps files — it only ever reads the dump side for comparison — so `:memory:` is used instead,
    with no temp file or cleanup needed.

    `row_factory` is set explicitly to `sqlite3.Row` to match `repository.connect()`'s real
    connections (`db.py`'s `connect()` sets this on every store connection unconditionally): the
    diff/snapshot/apply code below reads both sides through the same `row["col"]` access pattern,
    which a bare `sqlite3.connect()`'s default tuple rows would break silently (wrong-looking but
    still-truthy values from positional access, not a clean AttributeError)."""
    latest = project_root / ".pdata-db-dump" / "latest.sql"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dump.sql_body(latest.read_text()))
    return conn


def _row_to_base_dict(row: sqlite3.Row) -> dict[str, object]:
    return {
        "content": row["content"],
        "file_path": row["file_path"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "version": row["version"],
        "deleted_at": row["deleted_at"],
    }


def _all_record_ids(conn: sqlite3.Connection) -> set[int]:
    return {row["id"] for row in conn.execute("SELECT id FROM records")}


def _snapshot(conn: sqlite3.Connection, record_id: int) -> _Snapshot | None:
    row = repository.get_base_record(conn, record_id)
    if row is None:
        return None
    record_group = row["record_group"]
    ext_row = repository.get_extension_row(conn, record_group, record_id)
    extension = (
        {key: ext_row[key] for key in ext_row.keys() if key != "record_id"}
        if ext_row is not None else None
    )
    payload: dict[str, object] = {"base": _row_to_base_dict(row), "extension": extension}
    return _Snapshot(record_group=record_group, payload=payload)


def _classify(local_snap: _Snapshot | None, dump_snap: _Snapshot | None) -> tuple[bool, bool]:
    """Returns `(id_collision, is_delete_vs_update)` for one record_id's two snapshots.

    id_collision: true iff both sides have a row for this id but it is not the same logical
    record. `records.id` has no AUTOINCREMENT (repository.py's `_BASE_DDL`) — it is SQLite's bare
    rowid, assigned independently per-database from each database's own `max(rowid)+1`. Two
    machines that fork after a shared rehydrated ancestor and then each insert one or more
    brand-new records can legitimately allocate the SAME id to two entirely unrelated rows — this
    is not a hypothetical edge case, it is the expected outcome whenever both sides add the same
    number of new records to the same record_group after diverging (both start from the same
    max(id), both increment by the same count). `record_group` and `created_at` are both set once
    at insert time and never mutated by any write path in repository.py
    (`update_base_record`/`soft_delete`/`restore` all leave both alone) — so for the SAME logical
    record, both must be identical on both sides forever; either one differing is conclusive proof
    the id was independently assigned to two different rows, not proof of a genuine edit conflict
    on one row. `apply_resolution` refuses to touch these — see its own docstring/error text."""
    if local_snap is None or dump_snap is None:
        return False, False
    if local_snap.record_group != dump_snap.record_group:
        return True, False
    local_base = local_snap.payload["base"]
    dump_base = dump_snap.payload["base"]
    assert isinstance(local_base, dict) and isinstance(dump_base, dict)
    if local_base["created_at"] != dump_base["created_at"]:
        return True, False
    is_delete_vs_update = (local_base["deleted_at"] is None) != (dump_base["deleted_at"] is None)
    return False, is_delete_vs_update


def _diff_records(
    local_conn: sqlite3.Connection, dump_conn: sqlite3.Connection,
) -> list[RecordDiff]:
    diffs: list[RecordDiff] = []
    for record_id in sorted(_all_record_ids(local_conn) | _all_record_ids(dump_conn)):
        local_snap = _snapshot(local_conn, record_id)
        dump_snap = _snapshot(dump_conn, record_id)
        if local_snap == dump_snap:
            continue  # identical on both sides — nothing to resolve

        if local_snap is not None:
            record_group = local_snap.record_group
        else:
            assert dump_snap is not None  # the union of ids guarantees at least one side has it
            record_group = dump_snap.record_group

        id_collision, is_delete_vs_update = _classify(local_snap, dump_snap)
        diffs.append(RecordDiff(
            record_id=record_id,
            record_group=record_group,
            local=local_snap.payload if local_snap is not None else None,
            dump=dump_snap.payload if dump_snap is not None else None,
            is_delete_vs_update=is_delete_vs_update,
            id_collision=id_collision,
        ))
    return diffs


def _record_group_fields(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (row["record_group"], row["field_name"])
        for row in conn.execute("SELECT record_group, field_name FROM record_group_fields")
    }


def _diff_schema_fields(
    local_conn: sqlite3.Connection, dump_conn: sqlite3.Connection,
) -> list[SchemaFieldDiff]:
    local_fields = _record_group_fields(local_conn)
    dump_fields = _record_group_fields(dump_conn)
    diffs: list[SchemaFieldDiff] = []
    for record_group, field_name in sorted(local_fields ^ dump_fields):
        diffs.append(SchemaFieldDiff(
            record_group=record_group,
            field_name=field_name,
            present_locally=(record_group, field_name) in local_fields,
            present_in_dump=(record_group, field_name) in dump_fields,
        ))
    return diffs


def _apply_dump_choice(
    local_conn: sqlite3.Connection, dump_conn: sqlite3.Connection, record_diff: RecordDiff,
) -> None:
    """Writes the dump's side of one record — base row first, extension row second, matching the
    spec's dependency order ("schema before data... never write a row referencing a column that
    doesn't exist yet") within the extension step itself."""
    dump_payload = record_diff.dump
    assert dump_payload is not None  # validated by apply_resolution's `dumpless` check
    base = dump_payload["base"]
    assert isinstance(base, dict)

    if record_diff.local is None:
        local_conn.execute(
            "INSERT INTO records "
            "(id, record_group, content, file_path, created_at, updated_at, version, deleted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_diff.record_id, record_diff.record_group, base["content"],
                base["file_path"], base["created_at"], base["updated_at"], base["version"],
                base["deleted_at"],
            ),
        )
    else:
        local_conn.execute(
            "UPDATE records SET content=?, file_path=?, updated_at=?, version=?, deleted_at=? "
            "WHERE id=?",
            (
                base["content"], base["file_path"], base["updated_at"], base["version"],
                base["deleted_at"], record_diff.record_id,
            ),
        )

    extension = dump_payload["extension"]
    if extension is None:
        return
    assert isinstance(extension, dict)
    _apply_extension_fields(
        local_conn, dump_conn, record_diff.record_group, record_diff.record_id, extension,
    )


def _apply_extension_fields(
    local_conn: sqlite3.Connection,
    dump_conn: sqlite3.Connection,
    record_group: str,
    record_id: int,
    fields: dict[str, object],
) -> None:
    dump_types = _dump_extension_column_types(dump_conn, record_group)
    live_columns = set(repository.list_extension_columns(local_conn, record_group))
    for field_name in fields:
        if field_name not in live_columns:
            added = repository.add_extension_column(
                local_conn, record_group, field_name, dump_types[field_name], default=None,
            )
            if added:
                _copy_field_description(dump_conn, local_conn, record_group, field_name)

    if repository.get_extension_row(local_conn, record_group, record_id) is None:
        repository.insert_extension_row(local_conn, record_group, record_id, fields)
    else:
        repository.update_extension_row(local_conn, record_group, record_id, fields)


def _dump_extension_column_types(
    dump_conn: sqlite3.Connection, record_group: str,
) -> dict[str, str]:
    if not repository.extension_table_exists(dump_conn, record_group):
        return {}
    table = naming.extension_table_name(record_group)
    return {
        row["name"]: row["type"]
        for row in dump_conn.execute(f'PRAGMA table_info("{table}")')
        if row["name"] != "record_id"
    }


def _copy_field_description(
    dump_conn: sqlite3.Connection,
    local_conn: sqlite3.Connection,
    record_group: str,
    field_name: str,
) -> None:
    """Keeps `record_group_fields` (the schema catalog) reconciled alongside the column itself —
    spec: "a record can only be considered 'resolved' once its record_group's schema is
    reconciled on the side that adopts it". Best-effort: the dump may never have registered a
    description for this field either (`schema add-field --description` is optional), in which
    case there is nothing to copy and the column addition above is already the whole fix."""
    row = dump_conn.execute(
        "SELECT description, added_at FROM record_group_fields "
        "WHERE record_group=? AND field_name=?",
        (record_group, field_name),
    ).fetchone()
    if row is None:
        return
    repository.upsert_field_description(
        local_conn, record_group=record_group, field_name=field_name,
        description=row["description"], added_at=row["added_at"],
    )
