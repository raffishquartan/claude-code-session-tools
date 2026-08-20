from __future__ import annotations

import sqlite3
import tarfile

import pytest

from cc_session_tools.lib.pdata import backup


def test_backup_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "custom"))
    assert backup.backup_dir() == tmp_path / "custom"


def test_create_backup_writes_tar_containing_project_files(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    assert tar_path.exists()
    assert tar_path.parent == tmp_path / "backups"
    with tarfile.open(tar_path, "r:gz") as tar:
        names = tar.getnames()
        assert "demo/ideas.csv" in names


def test_create_backup_is_outside_project_root(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "demo"
    project_root.mkdir()
    tar_path = backup.create_backup(project="demo", project_root=project_root)
    assert project_root not in tar_path.parents


def test_create_backup_leaves_no_tmp_file_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    assert tar_path.exists()
    tmp_candidates = list(tar_path.parent.glob("*.tmp"))
    assert tmp_candidates == []


def test_create_backup_leaves_no_partial_file_at_final_name_on_failure(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    monkeypatch.setattr(backup.time, "sleep", lambda _seconds: None)
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    def always_fails(self, name, **kwargs):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(tarfile.TarFile, "add", always_fails)

    with pytest.raises(backup.BackupError, match="demo"):
        backup.create_backup(project="demo", project_root=project_root)

    backups_dir = tmp_path / "backups"
    assert list(backups_dir.glob("demo-*.tar.gz")) == []
    assert list(backups_dir.glob("*.tmp")) == []


def test_create_backup_retries_transient_os_error_then_succeeds(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    monkeypatch.setattr(backup.time, "sleep", lambda _seconds: None)
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    calls = {"n": 0}
    real_add = tarfile.TarFile.add

    def flaky_add(self, name, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(5, "Input/output error")
        # tarfile.TarFile.add recurses into directory children via self.add(...), which
        # would re-enter this mock (it's patched at class level) and inflate the call
        # count. Swap in the real implementation for the duration of this call so the
        # recursive children go straight through, then restore the mock afterwards.
        monkeypatch.setattr(tarfile.TarFile, "add", real_add)
        try:
            return real_add(self, name, *args, **kwargs)
        finally:
            monkeypatch.setattr(tarfile.TarFile, "add", flaky_add)

    monkeypatch.setattr(tarfile.TarFile, "add", flaky_add)

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    assert calls["n"] == 2
    assert tar_path.exists()


def test_create_backup_reports_each_failed_attempt_via_on_progress(monkeypatch, tmp_path):
    """Without this, --write's progress log shows "Backing up..." then a silent multi-second
    gap (one per retry's backoff) before either success or a single final BackupError — no
    record of how many attempts ran or what each one hit. on_progress must fire once per
    failed attempt, before that attempt's backoff sleep."""
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    monkeypatch.setattr(backup.time, "sleep", lambda _seconds: None)
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    def always_fails(self, name, **kwargs):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(tarfile.TarFile, "add", always_fails)

    messages: list[str] = []
    with pytest.raises(backup.BackupError):
        backup.create_backup(
            project="demo", project_root=project_root, on_progress=messages.append,
        )

    assert len(messages) == backup._MAX_ATTEMPTS
    assert all(f"attempt {n}/{backup._MAX_ATTEMPTS}" in messages[n - 1] for n in range(1, backup._MAX_ATTEMPTS + 1))


def test_create_backup_on_progress_defaults_to_silent(monkeypatch, tmp_path):
    """Every existing caller (init_service.write() before this change, and every test above
    this one) calls create_backup() with no on_progress — must keep working silently."""
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    tar_path = backup.create_backup(project="demo", project_root=project_root)  # no kwarg
    assert tar_path.exists()


def test_create_backup_includes_project_db_snapshot(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import store

    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "dbs"))
    project_root = tmp_path / "demo"
    project_root.mkdir()

    db_path = store.db_path("demo")
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    with tarfile.open(tar_path, "r:gz") as tar:
        # Under a distinct _pdata-db/ prefix, not demo/ — the .db snapshot is CCST's own
        # tool state, not project content, so it must never land in the same in-archive
        # location a human restoring demo/... into project_root would extract to.
        assert "_pdata-db/demo.db" in tar.getnames()
        assert "demo/demo.db" not in tar.getnames()
        extracted = tmp_path / "extracted.db"
        with tar.extractfile("_pdata-db/demo.db") as fh:
            extracted.write_bytes(fh.read())

    verify_conn = sqlite3.connect(extracted)
    assert verify_conn.execute("SELECT x FROM t").fetchall() == [(1,)]


def test_create_backup_wraps_sqlite_error_during_db_snapshot(monkeypatch, tmp_path):
    """sqlite3.OperationalError is NOT a subclass of OSError, so a locked/corrupt .db hit
    during the sqlite3 backup API call in _snapshot_db_into_tar must be handled exactly like
    an OSError by create_backup(): wrapped into BackupError (not left to propagate raw,
    which would bypass Task 3's `except backup.BackupError` in init_service.write()),
    retried per _MAX_ATTEMPTS/backoff, and cleaned up with no leftover .tmp file."""
    from cc_session_tools.lib.pdata import store

    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "dbs"))
    monkeypatch.setattr(backup.time, "sleep", lambda _seconds: None)
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "ideas.csv").write_text("idea\nfirst\n")

    db_path = store.db_path("demo")
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()

    calls = {"n": 0}

    class LockedConnection:
        """Stands in for the real readonly sqlite3.Connection db.connect() returns, so
        .backup() can be forced to raise sqlite3.OperationalError — sqlite3.Connection
        is a C-level immutable type and its methods can't be monkeypatched directly."""

        def backup(self, *args, **kwargs):
            calls["n"] += 1
            raise sqlite3.OperationalError("database is locked")

        def close(self):
            pass

    monkeypatch.setattr(backup.db, "connect", lambda *args, **kwargs: LockedConnection())

    with pytest.raises(backup.BackupError, match="demo"):
        backup.create_backup(project="demo", project_root=project_root)

    assert calls["n"] == backup._MAX_ATTEMPTS

    backups_dir = tmp_path / "backups"
    assert list(backups_dir.glob("demo-*.tar.gz")) == []
    assert list(backups_dir.glob("*.tmp")) == []


def test_create_backup_skips_db_entry_when_no_db_exists(monkeypatch, tmp_path):
    from cc_session_tools.lib.pdata import store

    monkeypatch.setenv(backup.BACKUP_DIR_ENV, str(tmp_path / "backups"))
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "dbs"))
    project_root = tmp_path / "demo"
    project_root.mkdir()

    tar_path = backup.create_backup(project="demo", project_root=project_root)

    with tarfile.open(tar_path, "r:gz") as tar:
        assert "_pdata-db/demo.db" not in tar.getnames()
