from __future__ import annotations

import os

import pytest

from cc_session_tools.lib.pdata import backup, init_paths, store


def test_default_projects_root(monkeypatch, tmp_path):
    monkeypatch.delenv(init_paths.PROJECTS_ROOT_ENV, raising=False)
    monkeypatch.setattr(
        "cc_session_tools.lib.pdata.init_paths.Path.home", lambda: tmp_path
    )
    assert init_paths.default_projects_root() == tmp_path / "cc"


def test_default_projects_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "custom"))
    assert init_paths.default_projects_root() == tmp_path / "custom"


def test_resolve_project_root_creates_default_location(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path))
    root = init_paths.resolve_project_root("demo", rehearse=None)
    assert root == tmp_path / "demo"
    assert root.is_dir()


def test_resolve_project_root_rehearse_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "real"))
    rehearsal = tmp_path / "rehearsal-copy"
    rehearsal.mkdir()
    root = init_paths.resolve_project_root("demo", rehearse=rehearsal)
    assert root == rehearsal


def test_resolve_project_root_rejects_unsafe_project_name(monkeypatch, tmp_path):
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path))
    with pytest.raises(ValueError, match="project"):
        init_paths.resolve_project_root("../escape", rehearse=None)


def test_project_db_dir_override_noop_without_rehearse(monkeypatch):
    monkeypatch.delenv(store.PROJECT_DB_DIR_ENV, raising=False)
    with init_paths.project_db_dir_override(None):
        assert store.PROJECT_DB_DIR_ENV not in os.environ


def test_project_db_dir_override_redirects_and_restores(monkeypatch, tmp_path):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, "/original/value")
    rehearse_path = tmp_path / "rehearsal"
    with init_paths.project_db_dir_override(rehearse_path):
        assert os.environ[store.PROJECT_DB_DIR_ENV] == str(
            rehearse_path / init_paths.REHEARSAL_DB_DIRNAME
        )
    assert os.environ[store.PROJECT_DB_DIR_ENV] == "/original/value"


def test_project_db_dir_override_clears_when_previously_unset(monkeypatch, tmp_path):
    monkeypatch.delenv(store.PROJECT_DB_DIR_ENV, raising=False)
    rehearse_path = tmp_path / "rehearsal"
    with init_paths.project_db_dir_override(rehearse_path):
        assert store.PROJECT_DB_DIR_ENV in os.environ
    assert store.PROJECT_DB_DIR_ENV not in os.environ


def test_backup_dir_override_noop_without_rehearse(monkeypatch):
    monkeypatch.delenv(backup.BACKUP_DIR_ENV, raising=False)
    with init_paths.backup_dir_override(None):
        assert backup.BACKUP_DIR_ENV not in os.environ


def test_backup_dir_override_redirects_and_restores(monkeypatch, tmp_path):
    monkeypatch.setenv(backup.BACKUP_DIR_ENV, "/original/backups")
    rehearse_path = tmp_path / "rehearsal"
    with init_paths.backup_dir_override(rehearse_path):
        assert os.environ[backup.BACKUP_DIR_ENV] == str(
            rehearse_path / init_paths.REHEARSAL_BACKUP_DIRNAME
        )
    assert os.environ[backup.BACKUP_DIR_ENV] == "/original/backups"


def test_backup_dir_override_clears_when_previously_unset(monkeypatch, tmp_path):
    monkeypatch.delenv(backup.BACKUP_DIR_ENV, raising=False)
    rehearse_path = tmp_path / "rehearsal"
    with init_paths.backup_dir_override(rehearse_path):
        assert backup.BACKUP_DIR_ENV in os.environ
    assert backup.BACKUP_DIR_ENV not in os.environ


def test_excluded_dir_names_includes_rehearsal_backup_dirname():
    """The classifier walks project_root via EXCLUDED_DIR_NAMES alone — without
    this, a rehearsal's own backup tarball directory would be walked into and its
    contents proposed as project files on a reclassification pass."""
    assert init_paths.REHEARSAL_BACKUP_DIRNAME in init_paths.EXCLUDED_DIR_NAMES
