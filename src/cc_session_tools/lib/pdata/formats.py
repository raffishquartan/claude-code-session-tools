"""pdata database formats and the gate that keeps unmigrated databases read-only.

Format 3 is the ccst 3.x integer-id schema (`records.id INTEGER PRIMARY KEY`); format 4 is the
UUID-id schema (`records.id TEXT PRIMARY KEY`). Each format is named for the ccst major version
that introduced it. The format of a database is read from the declared type of `records.id`;
the `pdata-uuid-ids` marker in the `migrations` ledger records that a database was created or
migrated as format 4, so `ccst doctor` can report a database whose schema and marker disagree.

A format 3 database is left exactly as 3.x wrote it until `ccst pdata migrate-uuid --write`
converts it. Until then every `ccst pdata` verb that reads or writes records refuses (see
FORMAT_GATE_EXEMPT_VERBS for the ones that do not).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Final

from cc_session_tools.lib import db
from cc_session_tools.lib.pdata import store

PDATA_FORMAT_INTEGER_IDS: Final = 3
PDATA_FORMAT_UUID_IDS: Final = 4
CURRENT_PDATA_FORMAT: Final = PDATA_FORMAT_UUID_IDS

UUID_IDS_MIGRATION: Final = "pdata-uuid-ids"

MIGRATE_COMMAND: Final = "ccst pdata migrate-uuid"
RUNBOOK_PATH: Final = "docs/pdata-uuid-migration-runbook.md"

# `ccst pdata` verbs that still run on a format 3 database: the migration itself, and the
# diagnostics a user needs while deciding to migrate. `sync-check` only reports status on a
# format 3 database. `readiness-scan` reads project files, never the database. `ccst doctor`
# is not a `ccst pdata` verb and is never gated.
FORMAT_GATE_EXEMPT_VERBS: Final = frozenset(
    {"migrate-uuid", "sync-check", "verify", "readiness-scan"}
)

_FORMAT_BY_ID_TYPE: Final = {"INTEGER": PDATA_FORMAT_INTEGER_IDS, "TEXT": PDATA_FORMAT_UUID_IDS}
_ID_TYPE_BY_FORMAT: Final = {fmt: sql_type for sql_type, fmt in _FORMAT_BY_ID_TYPE.items()}


class UnknownPdataFormatError(ValueError):
    """`records.id` is declared with a type no ccst release ever created."""


def pdata_format(conn: sqlite3.Connection) -> int:
    """The format of the open database. A database with no `records` table yet has nothing to
    migrate and is CURRENT_PDATA_FORMAT: the base schema creates it as format 4."""
    id_type = None
    for row in conn.execute("PRAGMA table_info(records)"):
        if row[1] == "id":
            id_type = str(row[2]).upper()
    if id_type is None:
        return CURRENT_PDATA_FORMAT
    fmt = _FORMAT_BY_ID_TYPE.get(id_type)
    if fmt is None:
        raise UnknownPdataFormatError(
            f"records.id is declared {id_type!r}, which is neither format 3 (INTEGER) nor "
            f"format 4 (TEXT)"
        )
    return fmt


def record_id_sql_type(conn: sqlite3.Connection) -> str:
    """The column type a table keyed by record id must use in this database, so a table created
    after the fact (an extension or verify table) matches `records.id`."""
    return _ID_TYPE_BY_FORMAT[pdata_format(conn)]


def database_format(path: Path) -> int | None:
    """The format of the database file at `path`, or None if there is no such file. Opens the
    file read-only, so it never creates or changes anything."""
    if not path.exists():
        return None
    conn = db.connect(path, readonly=True)
    try:
        return pdata_format(conn)
    finally:
        conn.close()


def projects_needing_migration(projects: Iterable[str]) -> list[str]:
    """The projects among `projects` whose database exists and is format 3, in input order."""
    return [
        project for project in projects
        if database_format(store.db_path(project)) == PDATA_FORMAT_INTEGER_IDS
    ]


def _migrate_instructions(project: str) -> str:
    return (
        f"From a plain terminal with no `claude` session running, run "
        f"`{MIGRATE_COMMAND} --project {project}` (dry run), then the same with --write. See "
        f"{RUNBOOK_PATH} in the cc-session-tools repository."
    )


def legacy_format_message(project: str) -> str:
    """Why a record verb refused on `project`'s format 3 database, and what to run."""
    return (
        f"{project}: database is format 3 (integer record ids); this ccst uses format 4 (UUID "
        f"record ids) and changes nothing until it is migrated. {_migrate_instructions(project)}"
    )


def migration_pending_detail(project: str, *, local_format: int, dump_format: int | None) -> str:
    """Why sync left `project` alone: its database and its published dump are not both the
    current format. `dump_format` is None when nothing is published. Callers prefix the project
    name and "migration pending" in their own style, so the text starts with neither."""
    if dump_format is None or dump_format == local_format:
        return (
            f"the database is format {local_format} and this ccst uses "
            f"format {CURRENT_PDATA_FORMAT}; nothing was loaded or published. "
            f"{_migrate_instructions(project)}"
        )
    return (
        f"the database is format {local_format} but the published dump is "
        f"format {dump_format}; nothing was loaded or published (this is not a fork). The "
        f"format {PDATA_FORMAT_INTEGER_IDS} side must be migrated. "
        f"{_migrate_instructions(project)}"
    )
