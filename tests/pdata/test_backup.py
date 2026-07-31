from __future__ import annotations

import tarfile

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
