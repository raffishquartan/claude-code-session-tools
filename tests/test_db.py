import sqlite3
import threading

import pytest

from cc_session_tools.lib import db

_DDL = """
CREATE TABLE IF NOT EXISTS widgets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
"""


def test_connect_creates_parent_dir_and_applies_pragmas(tmp_path):
    target = tmp_path / "nested" / "store.db"
    conn = db.connect(target, ddl=_DDL)
    try:
        assert target.exists()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1
    finally:
        conn.close()


def test_connect_runs_ddl_idempotently(tmp_path):
    target = tmp_path / "store.db"
    conn1 = db.connect(target, ddl=_DDL)
    conn1.execute("INSERT INTO widgets (name) VALUES ('a')")
    conn1.commit()
    conn1.close()

    # Re-running DDL on the same file must not error or wipe data.
    conn2 = db.connect(target, ddl=_DDL)
    rows = conn2.execute("SELECT name FROM widgets").fetchall()
    conn2.close()
    assert [r["name"] for r in rows] == ["a"]


def test_connect_row_factory_supports_dict_style_access(tmp_path):
    conn = db.connect(tmp_path / "store.db", ddl=_DDL)
    conn.execute("INSERT INTO widgets (name) VALUES ('x')")
    conn.commit()
    row = conn.execute("SELECT * FROM widgets").fetchone()
    conn.close()
    assert row["name"] == "x"


def test_connect_readonly_cannot_write(tmp_path):
    target = tmp_path / "store.db"
    setup = db.connect(target, ddl=_DDL)
    setup.close()

    conn = db.connect(target, readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO widgets (name) VALUES ('nope')")
    finally:
        conn.close()


def test_connect_readonly_missing_file_raises(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        db.connect(tmp_path / "missing.db", readonly=True)


def test_connect_rejects_old_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 30, 0))
    with pytest.raises(RuntimeError, match="too old"):
        db.connect(tmp_path / "store.db", ddl=_DDL)


def test_concurrent_writers_do_not_corrupt(tmp_path):
    target = tmp_path / "store.db"
    db.connect(target, ddl=_DDL).close()

    errors = []

    def writer(i):
        try:
            conn = db.connect(target)
            conn.execute("INSERT INTO widgets (name) VALUES (?)", (f"w{i}",))
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001 - captured for assertion, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    conn = db.connect(target)
    count = conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0]
    conn.close()
    assert count == 8


def test_backup_to_copies_committed_data(tmp_path):
    source = tmp_path / "source.db"
    dest = tmp_path / "backups" / "source-copy.db"

    conn = db.connect(source, ddl=_DDL)
    conn.execute("INSERT INTO widgets (name) VALUES ('backed-up')")
    conn.commit()
    conn.close()

    db.backup_to(source, dest)

    assert dest.exists()
    check = sqlite3.connect(str(dest))
    rows = check.execute("SELECT name FROM widgets").fetchall()
    check.close()
    assert rows == [("backed-up",)]


def test_checkpoint_does_not_error_on_fresh_connection(tmp_path):
    conn = db.connect(tmp_path / "store.db", ddl=_DDL)
    db.checkpoint(conn)  # must not raise
    conn.close()


def test_connect_closes_handle_when_pragma_setup_fails_on_corrupt_file(tmp_path):
    target = tmp_path / "corrupt.db"
    target.write_bytes(b"not a sqlite file")

    with pytest.raises(sqlite3.DatabaseError):
        db.connect(target, ddl=_DDL)

    # No leaked connection: SQLite's rollback journal / lock files must not
    # remain open, and re-opening the same path must not hang or conflict.
    with pytest.raises(sqlite3.DatabaseError):
        db.connect(target, ddl=_DDL)


def test_cold_start_concurrent_connect_no_lost_wal_switch(tmp_path):
    """20 threads all create the same fresh .db at once and each insert a row.

    The WAL-mode switch that runs on every connect must not drop a connection
    with SQLITE_BUSY under cold-start contention — SQLite ignores busy_timeout
    for a journal-mode change, so connect() retries it itself. Every write must
    land: no silently-lost rows.
    """
    target = tmp_path / "race" / "store.db"
    errors: list[Exception] = []

    def worker() -> None:
        try:
            conn = db.connect(target, ddl=_DDL)
            try:
                conn.execute("INSERT INTO widgets (name) VALUES ('w')")
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - captured for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    conn = db.connect(target, ddl=_DDL)
    try:
        count = conn.execute("SELECT COUNT(*) FROM widgets").fetchone()[0]
    finally:
        conn.close()
    assert count == 20
