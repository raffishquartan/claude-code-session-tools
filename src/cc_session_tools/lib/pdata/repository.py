"""SQLite data-access layer for per-project data stores (spec §4).

The single home of all SQL for the base records/record_group_fields tables and every
ext_<record_group> extension table. Callers go through service.py for validation; this module
trusts its inputs are already validated (record_group/field-name charset, project name).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from cc_session_tools.lib import db
from cc_session_tools.lib.pdata import naming, store

_ALLOWED_COLUMN_TYPES = frozenset({"TEXT", "INTEGER", "REAL", "BLOB"})


def _normalize_column_type(sql_type: str) -> str:
    """Whitelist-validate a column type token before it is interpolated into DDL (identifiers
    and types cannot be bound parameters in SQLite DDL — see plan Decision 2)."""
    normalized = sql_type.strip().upper()
    if normalized not in _ALLOWED_COLUMN_TYPES:
        raise ValueError(
            f"invalid column type {sql_type!r}: must be one of "
            f"{', '.join(sorted(_ALLOWED_COLUMN_TYPES))}"
        )
    return normalized

_BASE_DDL = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    record_group TEXT NOT NULL,
    content TEXT NOT NULL,
    file_path TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    deleted_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_records_group ON records(record_group);
CREATE INDEX IF NOT EXISTS idx_records_updated ON records(updated_at);

CREATE TABLE IF NOT EXISTS record_group_fields (
    record_group TEXT NOT NULL,
    field_name TEXT NOT NULL,
    description TEXT,
    added_at INTEGER NOT NULL,
    PRIMARY KEY (record_group, field_name)
);
"""


def connect(project: str) -> sqlite3.Connection:
    """Open <project>.db through the shared helper, in explicit-transaction mode.

    isolation_level=None turns off sqlite3's implicit BEGIN so callers issue their own
    BEGIN IMMEDIATE for multi-statement writes (see _immediate), matching
    lib/messaging/repository.py's connect()."""
    conn = db.connect(store.db_path(project), ddl=_BASE_DDL)
    conn.isolation_level = None
    return conn


@contextmanager
def _immediate(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the body inside a BEGIN IMMEDIATE / COMMIT, rolling back on error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def insert_base_record(
    conn: sqlite3.Connection,
    *,
    record_group: str,
    content: str,
    file_path: str | None,
    created_at: int,
    updated_at: int,
) -> int:
    """Insert one records row (caller already validated record_group). Returns the new id.
    Caller owns the transaction (wrap in _immediate if this isn't the only statement)."""
    cur = conn.execute(
        "INSERT INTO records (record_group, content, file_path, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (record_group, content, file_path, created_at, updated_at),
    )
    assert cur.lastrowid is not None  # sqlite3 always sets this after a successful INSERT
    return cur.lastrowid


def get_base_record(conn: sqlite3.Connection, record_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()


def ensure_extension_table(conn: sqlite3.Connection, record_group: str) -> None:
    """CREATE ext_<group> (record_id INTEGER PRIMARY KEY REFERENCES records(id)) if it doesn't
    exist yet — record_group is validated by naming.extension_table_name(). Caller owns the
    transaction.

    On the table's *first* creation only, backfills an ext row for every records row already
    in this group. Without this, a group that already had rows before its first
    `schema add-field` call would leave those rows without an ext row forever — breaking the
    one-to-one base/extension row invariant (plan Decision 3) for exactly the rows that existed
    first, and making `update --field` on any of them silently affect zero rows (a G1 silent-
    data-loss path). Checking existence explicitly first (rather than `CREATE TABLE IF NOT
    EXISTS` + unconditional backfill) is what makes the backfill run exactly once."""
    if extension_table_exists(conn, record_group):
        return
    table = naming.extension_table_name(record_group)
    conn.execute(
        f'CREATE TABLE "{table}" (record_id INTEGER PRIMARY KEY REFERENCES records(id))'
    )
    conn.execute(
        f'INSERT INTO "{table}" (record_id) SELECT id FROM records WHERE record_group=?',
        (record_group,),
    )


def extension_table_exists(conn: sqlite3.Connection, record_group: str) -> bool:
    table = naming.extension_table_name(record_group)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def list_extension_columns(conn: sqlite3.Connection, record_group: str) -> list[str]:
    """Live extension column names for record_group, excluding the record_id PK. Returns []
    if the extension table doesn't exist."""
    if not extension_table_exists(conn, record_group):
        return []
    table = naming.extension_table_name(record_group)
    return [
        r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')
        if r["name"] != "record_id"
    ]


def add_extension_column(
    conn: sqlite3.Connection,
    record_group: str,
    field_name: str,
    sql_type: str,
    *,
    default: object | None,
) -> None:
    """Idempotent: creates ext_<group> if missing (backfilling existing rows — see
    ensure_extension_table), then ADD COLUMN if field_name isn't already a column (no-op if it
    already exists — spec §5's schema add-field idempotency). Caller owns the transaction."""
    naming.validate_field_name(field_name)
    normalized_type = _normalize_column_type(sql_type)
    ensure_extension_table(conn, record_group)
    table = naming.extension_table_name(record_group)
    existing = set(list_extension_columns(conn, record_group))
    if field_name in existing:
        return
    if default is None:
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{field_name}" {normalized_type}')
    else:
        literal = _render_default_literal(default, normalized_type)
        conn.execute(
            f'ALTER TABLE "{table}" ADD COLUMN "{field_name}" {normalized_type} '
            f'DEFAULT {literal}'
        )


def _render_default_literal(value: object, normalized_type: str) -> str:
    """Render a DEFAULT clause literal for ALTER TABLE ADD COLUMN.

    SQLite does not accept a bound parameter in a DDL DEFAULT clause (confirmed: `ALTER TABLE t
    ADD COLUMN x INTEGER DEFAULT ?` raises `sqlite3.OperationalError: near "?": syntax error` —
    this is a hard SQLite grammar constraint, not a driver limitation). The literal must
    therefore be embedded directly in the SQL string. This stays injection-safe because
    normalized_type is already whitelist-checked (_normalize_column_type) and every branch
    below re-serializes value from a parsed Python value rather than ever passing the raw
    input string through unescaped:
    - TEXT: single-quote the string, doubling any embedded single quotes (SQL's own escape).
    - INTEGER/REAL: parse to a Python int/float first (raises ValueError on anything that
      isn't a valid number) and embed the *canonical* re-serialization, never the raw string.
    - BLOB: rejected — there's no safe, simple literal syntax to accept an arbitrary
      caller-supplied blob default, and no spec requirement to support one; omit --default for
      a BLOB field.
    """
    if normalized_type == "TEXT":
        return "'" + str(value).replace("'", "''") + "'"
    if normalized_type == "INTEGER":
        try:
            return str(int(str(value)))
        except ValueError as exc:
            raise ValueError(f"--default {value!r} is not a valid INTEGER") from exc
    if normalized_type == "REAL":
        try:
            return repr(float(str(value)))
        except ValueError as exc:
            raise ValueError(f"--default {value!r} is not a valid REAL") from exc
    raise ValueError(f"column defaults are not supported for type {normalized_type}")


def list_base_records(
    conn: sqlite3.Connection,
    *,
    record_group: str,
    since: int | None,
    until: int | None,
    limit: int | None,
    include_deleted: bool,
) -> list[sqlite3.Row]:
    clauses = ["record_group=?"]
    params: list[object] = [record_group]
    if not include_deleted:
        clauses.append("deleted_at IS NULL")
    if since is not None:
        clauses.append("updated_at >= ?")
        params.append(since)
    if until is not None:
        clauses.append("updated_at <= ?")
        params.append(until)
    where = " AND ".join(clauses)
    sql = f"SELECT * FROM records WHERE {where} ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def insert_extension_row(
    conn: sqlite3.Connection, record_group: str, record_id: int, fields: Mapping[str, object],
) -> None:
    """INSERT INTO ext_<group> (record_id, <given fields>) VALUES (...). Always creates a row
    (even with fields={}) so the group's base/ext rows stay 1:1 whenever ext_<group> exists —
    see plan Decision 3. Caller must have already validated every key in fields is a live
    column (service.py's job, not this layer's)."""
    table = naming.extension_table_name(record_group)
    columns = ["record_id", *fields.keys()]
    placeholders = ", ".join("?" for _ in columns)
    quoted_columns = ", ".join(f'"{c}"' for c in columns)
    conn.execute(
        f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})',
        (record_id, *fields.values()),
    )


def get_extension_row(
    conn: sqlite3.Connection, record_group: str, record_id: int,
) -> sqlite3.Row | None:
    if not extension_table_exists(conn, record_group):
        return None
    table = naming.extension_table_name(record_group)
    return conn.execute(
        f'SELECT * FROM "{table}" WHERE record_id=?', (record_id,)
    ).fetchone()


def upsert_field_description(
    conn: sqlite3.Connection, *, record_group: str, field_name: str,
    description: str | None, added_at: int,
) -> None:
    """Idempotent write to record_group_fields (spec §4.4) — overwrites description/added_at
    on re-run rather than duplicating the (record_group, field_name) row."""
    conn.execute(
        "INSERT INTO record_group_fields (record_group, field_name, description, added_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(record_group, field_name) "
        "DO UPDATE SET description=excluded.description, added_at=excluded.added_at",
        (record_group, field_name, description, added_at),
    )


def list_record_groups(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Every distinct record_group in this project's DB: row count (active rows only, per
    spec §4.5 default), whether it has an extension table, most recent updated_at.

    row_count uses COUNT(*) FILTER (WHERE deleted_at IS NULL), not a WHERE clause on the query
    itself — a WHERE deleted_at IS NULL placed before GROUP BY would drop a record_group whose
    only rows are all soft-deleted from the result set entirely (there being no non-deleted row
    left to GROUP BY), which would make it silently vanish from `schema list` instead of showing
    row_count=0. The FILTER form still groups every record_group that has any row at all — active
    or soft-deleted — and only restricts what gets counted."""
    rows = conn.execute(
        "SELECT record_group, "
        "COUNT(*) FILTER (WHERE deleted_at IS NULL) AS row_count, "
        "MAX(updated_at) AS max_updated_at "
        "FROM records GROUP BY record_group ORDER BY record_group"
    ).fetchall()
    return [
        {
            "record_group": r["record_group"],
            "row_count": r["row_count"],
            "max_updated_at": r["max_updated_at"],
            "has_extension_table": extension_table_exists(conn, r["record_group"]),
        }
        for r in rows
    ]


def show_schema_columns(conn: sqlite3.Connection, record_group: str) -> list[dict[str, object]]:
    """Base columns (fixed) + live extension columns (name/type from PRAGMA table_info,
    description from record_group_fields if set) for one record_group."""
    columns: list[dict[str, object]] = [
        {"source": "base", "name": name, "type": None, "description": None, "added_at": None}
        for name in naming.BASE_RECORD_COLUMNS
    ]
    if not extension_table_exists(conn, record_group):
        return columns

    table = naming.extension_table_name(record_group)
    descriptions = {
        r["field_name"]: (r["description"], r["added_at"])
        for r in conn.execute(
            "SELECT field_name, description, added_at FROM record_group_fields "
            "WHERE record_group=?",
            (record_group,),
        ).fetchall()
    }
    for r in conn.execute(f'PRAGMA table_info("{table}")'):
        if r["name"] == "record_id":
            continue
        description, added_at = descriptions.get(r["name"], (None, None))
        columns.append({
            "source": "extension", "name": r["name"], "type": r["type"],
            "description": description, "added_at": added_at,
        })
    return columns
