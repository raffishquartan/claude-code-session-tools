# src/cc_session_tools/lib/messaging/repository.py
"""SQLite data-access layer for the inter-session message store (ccmsg.db).

The single home of all SQL. Every mutation runs inside a BEGIN IMMEDIATE
transaction so concurrent writers serialise under WAL: this is what closes the
old retention-vs-claim double-unlink race (R1) and makes auto-read attribution
first-writer-wins (R2) without any file-based coordination. Rows map 1:1 to the
Message dataclass; the body lives in a TEXT column (attachments stay as
absolute-path references, JSON-encoded, never embedded)."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from cc_session_tools.lib import db
from cc_session_tools.lib.messaging import store
from cc_session_tools.lib.messaging.message import Message

_DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    "schema"        INTEGER NOT NULL,
    from_project    TEXT NOT NULL,
    from_session    TEXT NOT NULL,
    from_uuid       TEXT NOT NULL,
    to_kind         TEXT NOT NULL CHECK (to_kind IN ('session','project','description')),
    to_value        TEXT NOT NULL,
    to_location     TEXT NOT NULL,
    subject         TEXT NOT NULL,
    sent_at         TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('sent','read','claimed','archived')),
    read_at         TEXT,
    read_by_uuid    TEXT,
    read_by_session TEXT,
    claimed_at      TEXT,
    receipt_shown   INTEGER NOT NULL DEFAULT 0 CHECK (receipt_shown IN (0,1)),
    thread          TEXT,
    attachments     TEXT NOT NULL DEFAULT '[]',
    body            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_messages_sweep    ON messages(to_location, id);
CREATE INDEX IF NOT EXISTS idx_messages_status   ON messages(to_location, status);
CREATE INDEX IF NOT EXISTS idx_messages_receipts ON messages(from_uuid, receipt_shown);

CREATE TABLE IF NOT EXISTS cursors (
    session_uuid          TEXT NOT NULL,
    partition             TEXT NOT NULL,
    high_water_message_id TEXT NOT NULL,
    PRIMARY KEY (session_uuid, partition)
);
"""


class MessageNotFoundError(Exception):
    """Raised when a message id resolves to no row."""


def connect() -> sqlite3.Connection:
    """Open ccmsg.db through the shared helper, in explicit-transaction mode.

    isolation_level=None turns off sqlite3's implicit BEGIN so every mutation
    can issue its own BEGIN IMMEDIATE (see _immediate)."""
    conn = db.connect(store.db_path(), ddl=_DDL)
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


def _row_to_message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        schema=row["schema"],
        from_project=row["from_project"],
        from_session=row["from_session"],
        from_uuid=row["from_uuid"],
        to_kind=row["to_kind"],
        to_value=row["to_value"],
        to_location=row["to_location"],
        subject=row["subject"],
        sent_at=row["sent_at"],
        status=row["status"],
        read_at=row["read_at"],
        read_by_uuid=row["read_by_uuid"],
        read_by_session=row["read_by_session"],
        claimed_at=row["claimed_at"],
        receipt_shown=bool(row["receipt_shown"]),
        thread=row["thread"],
        attachments=list(json.loads(row["attachments"])),
        body=row["body"],
    )


_COLUMNS = (
    'id', '"schema"', 'from_project', 'from_session', 'from_uuid', 'to_kind',
    'to_value', 'to_location', 'subject', 'sent_at', 'status', 'read_at',
    'read_by_uuid', 'read_by_session', 'claimed_at', 'receipt_shown', 'thread',
    'attachments', 'body',
)


def _insert_params(message: Message) -> tuple[object, ...]:
    return (
        message.id, message.schema, message.from_project, message.from_session,
        message.from_uuid, message.to_kind, message.to_value, message.to_location,
        message.subject, message.sent_at, message.status, message.read_at,
        message.read_by_uuid, message.read_by_session, message.claimed_at,
        int(message.receipt_shown), message.thread,
        json.dumps(list(message.attachments)), message.body,
    )


def insert(message: Message) -> None:
    placeholders = ", ".join("?" for _ in _COLUMNS)
    sql = f"INSERT INTO messages ({', '.join(_COLUMNS)}) VALUES ({placeholders})"
    conn = connect()
    try:
        with _immediate(conn):
            conn.execute(sql, _insert_params(message))
    finally:
        conn.close()


def get_by_id(message_id: str) -> Message | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_message(row) if row is not None else None


def list_rows(
    *,
    status: str | None = None,
    partition: str | None = None,
    from_uuid: str | None = None,
) -> list[Message]:
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("status=?")
        params.append(status)
    if partition is not None:
        clauses.append("to_location=?")
        params.append(partition)
    if from_uuid is not None:
        clauses.append("from_uuid=?")
        params.append(from_uuid)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM messages{where} ORDER BY id", params
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_message(r) for r in rows]
