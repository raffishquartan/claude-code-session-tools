from __future__ import annotations

import sqlite3

from cc_session_tools.lib.pdata import sync_lock


def test_is_locked_false_when_nothing_else_holds_it(tmp_path):
    db_path = tmp_path / "p.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    try:
        assert sync_lock.is_locked(db_path) is False
    finally:
        conn.close()


def test_is_locked_true_while_another_connection_holds_begin_immediate(tmp_path):
    db_path = tmp_path / "p.db"
    holder = sqlite3.connect(db_path)
    holder.execute("CREATE TABLE t(x)")
    holder.execute("BEGIN IMMEDIATE")
    try:
        assert sync_lock.is_locked(db_path) is True
    finally:
        holder.execute("ROLLBACK")
        holder.close()
