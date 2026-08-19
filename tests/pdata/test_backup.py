from __future__ import annotations

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
