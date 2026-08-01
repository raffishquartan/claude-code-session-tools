from __future__ import annotations

import pytest

from cc_session_tools.lib import roots
from cc_session_tools.lib.pdata import session_output


def _make_project(base, name, *, with_sessions: bool) -> None:
    proj = base / name
    proj.mkdir(parents=True)
    if with_sessions:
        (proj / "cc-sessions").mkdir()


def test_discover_projects_with_sessions_filters_to_cc_sessions_dirs(monkeypatch, tmp_path):
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    _make_project(repo_root, "has-sessions", with_sessions=True)
    _make_project(repo_root, "no-sessions", with_sessions=False)
    monkeypatch.setenv(roots.REPO_ROOT_ENV, str(repo_root))
    monkeypatch.delenv(roots.PROJ_ROOT_ENV, raising=False)

    found = session_output.discover_projects_with_sessions()

    names = [name for name, _ in found]
    assert names == ["has-sessions"]


def test_discover_projects_with_sessions_dedupes_across_roots(monkeypatch, tmp_path):
    repo_root = tmp_path / "repos"
    proj_root = tmp_path / "cc-claude-code"
    repo_root.mkdir()
    proj_root.mkdir()
    _make_project(repo_root, "shared-name", with_sessions=True)
    _make_project(proj_root, "shared-name", with_sessions=True)
    monkeypatch.setenv(roots.REPO_ROOT_ENV, str(repo_root))
    monkeypatch.setenv(roots.PROJ_ROOT_ENV, str(proj_root))

    found = session_output.discover_projects_with_sessions()

    names = [name for name, _ in found]
    assert names.count("shared-name") == 1
    # REPO_ROOT_ENV is processed first in roots.load_session_roots()'s own ordering.
    assert dict(found)["shared-name"] == repo_root / "shared-name"


def test_discover_projects_with_sessions_raises_when_no_roots_configured(monkeypatch):
    monkeypatch.delenv(roots.REPO_ROOT_ENV, raising=False)
    monkeypatch.delenv(roots.PROJ_ROOT_ENV, raising=False)
    with pytest.raises(roots.RootsConfigError):
        session_output.discover_projects_with_sessions()


def test_find_project_root_returns_none_for_unknown_project(monkeypatch, tmp_path):
    repo_root = tmp_path / "repos"
    repo_root.mkdir()
    _make_project(repo_root, "known", with_sessions=True)
    monkeypatch.setenv(roots.REPO_ROOT_ENV, str(repo_root))
    monkeypatch.delenv(roots.PROJ_ROOT_ENV, raising=False)

    assert session_output.find_project_root("known") == repo_root / "known"
    assert session_output.find_project_root("unknown") is None
