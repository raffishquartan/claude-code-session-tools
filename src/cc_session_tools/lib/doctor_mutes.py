"""Persistent mute store for ``ccst doctor --drift``.

A *mute* records that the user has acknowledged a specific doctor check (by its
stable ``name``, e.g. ``version:pypi`` or ``skill:foo``) and does not want the
drift monitor to flag it again until it is un-muted.

Backed by the doctor_mutes table in sessions.db (consolidated per the
data-store-uplift migration — see docs/superpowers/plans/2026-07-13-data-store-uplift-04-sessions-db.md
design decision D7). File I/O lives here so the check/filter/format logic in
:mod:`doctor` stays pure. Function signatures are unchanged from the pre-SQLite
version; only ``path`` now names a sqlite db file instead of a JSON file.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cc_session_tools.lib import sessions_db


def default_mutes_path() -> Path:
    """Canonical mute-store path: the shared sessions.db file."""
    return sessions_db.default_db_path()


def load_mutes(path: Path) -> dict[str, str]:
    """Return the mute map (check name -> ISO date). Empty if the store has
    never been written to."""
    try:
        conn = sessions_db.connect(path=path, readonly=True)
    except sqlite3.OperationalError:
        return {}
    try:
        rows = conn.execute("SELECT name, muted_at FROM doctor_mutes").fetchall()
        return {r["name"]: r["muted_at"] for r in rows}
    finally:
        conn.close()


def add_mute(path: Path, name: str, *, today: str) -> dict[str, str]:
    """Mute ``name`` (recording ``today`` as the mute date) and persist."""
    conn = sessions_db.connect(path=path)
    try:
        conn.execute(
            "INSERT INTO doctor_mutes (name, muted_at) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET muted_at=excluded.muted_at",
            (name, today),
        )
        conn.commit()
    finally:
        conn.close()
    return load_mutes(path)


def remove_mute(path: Path, name: str) -> bool:
    """Un-mute ``name``. Return True if it was muted, False if it was not."""
    conn = sessions_db.connect(path=path)
    try:
        cur = conn.execute("DELETE FROM doctor_mutes WHERE name = ?", (name,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
