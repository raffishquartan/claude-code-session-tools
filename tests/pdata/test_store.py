from __future__ import annotations

import pytest

from cc_session_tools.lib.pdata import store


def test_db_path_default_location(monkeypatch, tmp_path):
    monkeypatch.delenv(store.PROJECT_DB_DIR_ENV, raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    assert store.db_path("pbt") == tmp_path / "project-db" / "pbt.db"


def test_db_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(store.PROJECT_DB_DIR_ENV, str(tmp_path / "custom"))
    assert store.db_path("pbt") == tmp_path / "custom" / "pbt.db"


@pytest.mark.parametrize("bad_name", ["", ".", "..", "a/b", "../escape", "/abs"])
def test_db_path_rejects_unsafe_project_names(bad_name):
    with pytest.raises(ValueError, match="project"):
        store.db_path(bad_name)


def test_db_path_accepts_normal_project_names():
    # Must not raise for the real project names this system deals with.
    for name in ("pbt", "maxella", "deauppet", "oneshot", "claude", "home"):
        store.db_path(name)


def test_project_root_default_location(monkeypatch, tmp_path, mocker):
    monkeypatch.delenv(store.PROJECTS_ROOT_ENV, raising=False)
    mocker.patch("pathlib.Path.home", return_value=tmp_path)
    assert store.project_root("pbt") == tmp_path / "cc" / "pbt"


def test_project_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path / "custom"))
    assert store.project_root("pbt") == tmp_path / "custom" / "pbt"


def test_project_root_rejects_unsafe_project_names():
    with pytest.raises(ValueError, match="project"):
        store.project_root("../escape")


def test_project_root_never_creates_the_directory(monkeypatch, tmp_path):
    monkeypatch.setenv(store.PROJECTS_ROOT_ENV, str(tmp_path))
    root = store.project_root("pbt")
    assert root == tmp_path / "pbt"
    assert not root.exists()
