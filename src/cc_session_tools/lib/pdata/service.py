"""Business logic for ccst pdata: validation, orchestration, and join-and-flatten on top of
repository.py's raw SQL. The CLI layer (ccst.py) stays a thin argparse wrapper around this
module, matching lib/messaging/service.py's split."""
from __future__ import annotations

import sqlite3
import time
import re as _re
from collections.abc import Mapping
from dataclasses import dataclass, field

from cc_session_tools.lib.pdata import naming, repository

# op is deliberately \S+ here (not the literal alternation of valid ops) — matching only a
# literal alternation would make an invalid op (e.g. "~=") fail to match the whole regex at all,
# so the caller falls into the generic "malformed clause" branch and the dedicated "invalid
# operator" error below becomes unreachable dead code. Capturing any non-space token as op and
# checking membership afterward is what makes both error messages actually reachable.
_WHERE_CLAUSE_RE = _re.compile(
    r"^(?P<field>\S+)\s+(?P<op>\S+)\s+(?P<value>.+)$",
)


def _parse_where_clause(raw: str) -> tuple[str, str, str]:
    match = _WHERE_CLAUSE_RE.match(raw.strip())
    if not match:
        raise ValueError(
            f"malformed --where clause (want '<field> <op> <value>'): {raw!r}"
        )
    field_name = match.group("field")
    op = match.group("op").upper()
    value = match.group("value")
    if op not in repository._WHERE_OPS:
        raise ValueError(f"invalid --where operator {op!r}: {raw!r}")
    return field_name, op, value


@dataclass
class Record:
    id: int
    record_group: str
    content: str
    file_path: str | None
    created_at: int
    updated_at: int
    version: int
    deleted_at: int | None
    fields: dict[str, object] = field(default_factory=dict)


def _validate_relative_file_path(file_path: str | None) -> None:
    """Boundary check mirroring Decision 1's project-name path-traversal guard: file_path is
    later resolved against the project root (project_root / record.file_path, per spec §4.2 —
    see Plan B), so a relative-but-escaping value like '../../etc/passwd' must be rejected here
    too, not just a leading '/'. Splitting on '/' (not os.sep) is deliberate: file_path is a
    stored, portable identifier, not a native OS path, so it always uses '/' regardless of the
    host platform."""
    if file_path is None:
        return
    if file_path.startswith("/"):
        raise ValueError(
            f"--file must be relative to the project root, got absolute path: {file_path!r}"
        )
    if any(segment == ".." for segment in file_path.split("/")):
        raise ValueError(
            f"--file must not contain '..' path-traversal segments: {file_path!r}"
        )


def _row_to_record(row: sqlite3.Row) -> Record:
    return Record(
        id=row["id"],
        record_group=row["record_group"],
        content=row["content"],
        file_path=row["file_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
        deleted_at=row["deleted_at"],
        fields={},
    )


def add_record(
    *,
    project: str,
    record_group: str,
    content: str,
    file_path: str | None,
    fields: Mapping[str, str],
    created_at: int | None = None,
) -> Record:
    naming.validate_record_group(record_group)
    _validate_relative_file_path(file_path)
    ts = created_at if created_at is not None else int(time.time())

    conn = repository.connect(project)
    try:
        with repository._immediate(conn):
            live_columns = set(repository.list_extension_columns(conn, record_group))
            unregistered = set(fields) - live_columns
            if unregistered:
                raise ValueError(
                    f"unregistered field(s) for group {record_group!r}: "
                    f"{sorted(unregistered)} — run 'ccst pdata schema add-field' first"
                )
            record_id = repository.insert_base_record(
                conn, record_group=record_group, content=content, file_path=file_path,
                created_at=ts, updated_at=ts,
            )
            if repository.extension_table_exists(conn, record_group):
                repository.insert_extension_row(conn, record_group, record_id, fields)
        row = repository.get_base_record(conn, record_id)
        assert row is not None
        ext_row = repository.get_extension_row(conn, record_group, record_id)
        record = _row_to_record(row)
        if ext_row is not None:
            record.fields = {k: ext_row[k] for k in ext_row.keys() if k != "record_id"}
    finally:
        conn.close()
    return record


def get_record(
    *, project: str, record_id: int, include_deleted: bool = False,
) -> Record | None:
    conn = repository.connect(project)
    try:
        row = repository.get_base_record(conn, record_id)
        if row is None:
            return None
        if row["deleted_at"] is not None and not include_deleted:
            return None
        record = _row_to_record(row)
        ext_row = repository.get_extension_row(conn, record.record_group, record_id)
        if ext_row is not None:
            record.fields = {k: ext_row[k] for k in ext_row.keys() if k != "record_id"}
        return record
    finally:
        conn.close()


def record_to_dict(record: Record) -> dict[str, object]:
    """Flatten a Record into one dict (base columns + extension fields merged) for CLI
    rendering. The single home of this shape — every ccst.py handler that prints a Record
    (get/list/query, and the current side of an update/delete conflict) calls this instead of
    each re-deriving its own flatten, per this repo's "one helper per shared shape" coding
    standard."""
    from dataclasses import asdict
    d = asdict(record)
    fields = d.pop("fields")
    d.update(fields)
    return d


def list_records(
    *,
    project: str,
    record_group: str,
    since: int | None = None,
    until: int | None = None,
    limit: int | None = None,
    include_deleted: bool = False,
) -> list[Record]:
    naming.validate_record_group(record_group)
    conn = repository.connect(project)
    try:
        rows = repository.list_base_records(
            conn, record_group=record_group, since=since, until=until,
            limit=limit, include_deleted=include_deleted,
        )
        has_ext = repository.extension_table_exists(conn, record_group)
        records = []
        for row in rows:
            record = _row_to_record(row)
            if has_ext:
                ext_row = repository.get_extension_row(conn, record_group, row["id"])
                if ext_row is not None:
                    record.fields = {
                        k: ext_row[k] for k in ext_row.keys() if k != "record_id"
                    }
            records.append(record)
        return records
    finally:
        conn.close()


def query_records(
    *, project: str, record_group: str, where: list[str], limit: int | None = None,
    include_deleted: bool = False,
) -> list[Record]:
    naming.validate_record_group(record_group)
    conditions = [_parse_where_clause(clause) for clause in where]
    conn = repository.connect(project)
    try:
        rows = repository.query_records(
            conn, record_group=record_group, conditions=conditions, limit=limit,
            include_deleted=include_deleted,
        )
        has_ext = repository.extension_table_exists(conn, record_group)
        records = []
        for row in rows:
            record = _row_to_record(row)
            if has_ext:
                ext_row = repository.get_extension_row(conn, record_group, row["id"])
                if ext_row is not None:
                    record.fields = {
                        k: ext_row[k] for k in ext_row.keys() if k != "record_id"
                    }
            records.append(record)
        return records
    finally:
        conn.close()


class RecordNotFoundError(Exception):
    """Raised when a record id resolves to no row (or no active row)."""


class VersionConflictError(Exception):
    """Raised on an update()/delete() optimistic-concurrency conflict (spec §6.2). Carries the
    current on-disk row and what the caller attempted, both flattened dicts, for the CLI to
    render as a diff."""

    def __init__(self, current: Mapping[str, object], attempted: Mapping[str, object]):
        super().__init__(f"version conflict on record {current.get('id')}")
        self.current = current
        self.attempted = attempted


def update_record(
    *,
    project: str,
    record_id: int,
    expected_version: int,
    content: str | None,
    file_path: str | None,
    fields: Mapping[str, str],
    updated_at: int | None = None,
) -> Record:
    """content and file_path are each optional (spec §5's `[--content "..."]  [--file <path>]`)
    — omitting one (passing None) leaves that column unchanged; it does not clear it. At least
    one of content, file_path, or fields must be given, or this is a no-op update request that
    only bumps version/updated_at for nothing (this repo's coding standard: reject inputs that
    ask the system to do nothing)."""
    if content is None and file_path is None and not fields:
        raise ValueError(
            "ccst pdata update requires at least one of --content, --file, or --field"
        )
    _validate_relative_file_path(file_path)
    ts = updated_at if updated_at is not None else int(time.time())

    conn = repository.connect(project)
    try:
        existing = repository.get_base_record(conn, record_id)
        if existing is None or existing["deleted_at"] is not None:
            raise RecordNotFoundError(record_id)
        record_group = existing["record_group"]

        live_columns = set(repository.list_extension_columns(conn, record_group))
        unregistered = set(fields) - live_columns
        if unregistered:
            raise ValueError(
                f"unregistered field(s) for group {record_group!r}: "
                f"{sorted(unregistered)} — run 'ccst pdata schema add-field' first"
            )

        with repository._immediate(conn):
            ok = repository.update_base_record(
                conn, record_id=record_id, expected_version=expected_version,
                content=content, file_path=file_path, updated_at=ts,
            )
            if ok and fields:
                repository.update_extension_row(conn, record_group, record_id, fields)

        if not ok:
            current_row = repository.get_base_record(conn, record_id)
            assert current_row is not None
            # The existence/soft-delete check above ran before this _immediate block acquired
            # its write lock, so a concurrent soft-delete can land in that narrow window: the
            # UPDATE's own `AND deleted_at IS NULL` clause then affects 0 rows for a reason that
            # isn't actually a version mismatch. Re-check deleted_at here (now inside the lock,
            # so this read is race-free) and report the accurate error rather than always
            # assuming a conflict.
            if current_row["deleted_at"] is not None:
                raise RecordNotFoundError(record_id)
            current = record_to_dict(_row_to_record(current_row))
            ext_row = repository.get_extension_row(conn, record_group, record_id)
            if ext_row is not None:
                current.update({k: ext_row[k] for k in ext_row.keys() if k != "record_id"})
            # A None content/file_path means "unchanged" (see the docstring above) — reflect
            # what would actually have landed on disk in the conflict diff, not a misleading
            # literal None, by falling back to the pre-update existing value for display.
            attempted = {
                "id": record_id,
                "content": content if content is not None else existing["content"],
                "file_path": file_path if file_path is not None else existing["file_path"],
                **fields,
            }
            raise VersionConflictError(current=current, attempted=attempted)

        updated_row = repository.get_base_record(conn, record_id)
        assert updated_row is not None
        record = _row_to_record(updated_row)
        ext_row = repository.get_extension_row(conn, record_group, record_id)
        if ext_row is not None:
            record.fields = {k: ext_row[k] for k in ext_row.keys() if k != "record_id"}
        return record
    finally:
        conn.close()


def delete_record(
    *, project: str, record_id: int, expected_version: int, deleted_at: int | None = None,
) -> None:
    ts = deleted_at if deleted_at is not None else int(time.time())
    conn = repository.connect(project)
    try:
        existing = repository.get_base_record(conn, record_id)
        if existing is None or existing["deleted_at"] is not None:
            raise RecordNotFoundError(record_id)

        with repository._immediate(conn):
            ok = repository.soft_delete(
                conn, record_id=record_id, expected_version=expected_version, deleted_at=ts,
            )
        if not ok:
            current_row = repository.get_base_record(conn, record_id)
            assert current_row is not None
            # Same race as update_record: the existence check above ran before this _immediate
            # block took its write lock, so a concurrent soft-delete in that window makes
            # soft_delete's own `AND deleted_at IS NULL` clause affect 0 rows. Re-check
            # deleted_at (race-free now that we hold the lock) so an already-deleted record
            # reports RecordNotFoundError rather than a misleading version conflict.
            if current_row["deleted_at"] is not None:
                raise RecordNotFoundError(record_id)
            current = record_to_dict(_row_to_record(current_row))
            attempted = {"id": record_id, "deleted_at": ts}
            raise VersionConflictError(current=current, attempted=attempted)
    finally:
        conn.close()


def restore_record(*, project: str, record_id: int, restored_at: int | None = None) -> None:
    ts = restored_at if restored_at is not None else int(time.time())
    conn = repository.connect(project)
    try:
        existing = repository.get_base_record(conn, record_id)
        if existing is None or existing["deleted_at"] is None:
            raise RecordNotFoundError(record_id)
        with repository._immediate(conn):
            repository.restore(conn, record_id=record_id, restored_at=ts)
    finally:
        conn.close()


def schema_add_field(
    *,
    project: str,
    record_group: str,
    field_name: str,
    sql_type: str,
    description: str | None,
    default: object | None,
) -> None:
    naming.validate_record_group(record_group)
    naming.validate_field_name(field_name)
    now = int(time.time())
    conn = repository.connect(project)
    try:
        with repository._immediate(conn):
            repository.add_extension_column(
                conn, record_group, field_name, sql_type, default=default,
            )
            if description is not None:
                repository.upsert_field_description(
                    conn, record_group=record_group, field_name=field_name,
                    description=description, added_at=now,
                )
    finally:
        conn.close()


def schema_list(*, project: str) -> list[dict[str, object]]:
    conn = repository.connect(project)
    try:
        return repository.list_record_groups(conn)
    finally:
        conn.close()


def schema_show(*, project: str, record_group: str) -> list[dict[str, object]]:
    naming.validate_record_group(record_group)
    conn = repository.connect(project)
    try:
        return repository.show_schema_columns(conn, record_group)
    finally:
        conn.close()
