"""Business logic for ccst pdata: validation, orchestration, and join-and-flatten on top of
repository.py's raw SQL. The CLI layer (ccst.py) stays a thin argparse wrapper around this
module, matching lib/messaging/service.py's split."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from cc_session_tools.lib.pdata import naming, repository


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
            record_id = repository.insert_base_record(
                conn, record_group=record_group, content=content, file_path=file_path,
                created_at=ts, updated_at=ts,
            )
        row = repository.get_base_record(conn, record_id)
        assert row is not None  # just inserted in this same connection
    finally:
        conn.close()
    return _row_to_record(row)


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
