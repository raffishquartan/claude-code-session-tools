from __future__ import annotations

import os

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


def _touch(path, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("content")
    os.utime(path, (mtime, mtime))


def test_find_new_out_files_only_returns_files_newer_than_watermark(tmp_path):
    project_root = tmp_path / "proj"
    old_file = project_root / "cc-sessions" / "20260701-a" / "out" / "old.md"
    new_file = project_root / "cc-sessions" / "20260710-b" / "out" / "new.md"
    _touch(old_file, 1000)
    _touch(new_file, 2000)

    found = session_output.find_new_out_files(project_root, since_mtime=1500)

    paths = [p for p, _ in found]
    assert new_file in paths
    assert old_file not in paths


def test_find_new_out_files_ignores_non_out_dirs_and_missing_cc_sessions(tmp_path):
    project_root = tmp_path / "proj"
    working_file = project_root / "cc-sessions" / "20260701-a" / "working" / "WORKLOG.md"
    _touch(working_file, 5000)

    found = session_output.find_new_out_files(project_root, since_mtime=0)

    assert found == []


def test_find_new_out_files_returns_empty_list_for_project_with_no_cc_sessions(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    assert session_output.find_new_out_files(project_root, since_mtime=0) == []


def test_session_tag_from_relpath_extracts_the_session_directory_name():
    rel = "cc-sessions/20260710-foo-bar/out/report.md"
    assert session_output.session_tag_from_relpath(rel) == "20260710-foo-bar"


@pytest.mark.parametrize(
    "bad_rel",
    ["out/report.md", "cc-sessions/report.md", "working/foo/out/x.md"],
)
def test_session_tag_from_relpath_rejects_malformed_paths(bad_rel):
    with pytest.raises(ValueError, match="cc-sessions"):
        session_output.session_tag_from_relpath(bad_rel)
