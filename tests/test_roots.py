from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import roots
from cc_session_tools.lib.roots import (
    PROJ_ROOT_ENV,
    REPO_ROOT_ENV,
    is_strict_root,
    load_session_roots,
    proj_root,
    repo_root,
)


class TestRepoRoot:
    def test_returns_none_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(REPO_ROOT_ENV, raising=False)
        assert repo_root() is None

    def test_returns_none_when_env_empty_string(self, monkeypatch):
        monkeypatch.setenv(REPO_ROOT_ENV, "")
        assert repo_root() is None

    def test_returns_resolved_path_when_env_points_at_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "repos"
        d.mkdir()
        monkeypatch.setenv(REPO_ROOT_ENV, str(d))
        assert repo_root() == d.resolve()

    def test_expands_tilde(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        (home / "repos").mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv(REPO_ROOT_ENV, "~/repos")
        assert repo_root() == (home / "repos").resolve()

    def test_returns_none_when_path_does_not_exist(self, tmp_path, monkeypatch):
        monkeypatch.setenv(REPO_ROOT_ENV, str(tmp_path / "no-such-dir"))
        assert repo_root() is None

    def test_returns_none_when_path_is_a_file(self, tmp_path, monkeypatch):
        f = tmp_path / "not-a-dir"
        f.write_text("hi")
        monkeypatch.setenv(REPO_ROOT_ENV, str(f))
        assert repo_root() is None

    def test_resolves_symlinks(self, tmp_path, monkeypatch):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        monkeypatch.setenv(REPO_ROOT_ENV, str(link))
        assert repo_root() == real.resolve()


class TestProjRoot:
    def test_returns_none_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(PROJ_ROOT_ENV, raising=False)
        assert proj_root() is None

    def test_returns_resolved_path_when_env_points_at_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "proj"
        d.mkdir()
        monkeypatch.setenv(PROJ_ROOT_ENV, str(d))
        assert proj_root() == d.resolve()

    def test_returns_none_when_path_does_not_exist(self, tmp_path, monkeypatch):
        monkeypatch.setenv(PROJ_ROOT_ENV, str(tmp_path / "no-such-dir"))
        assert proj_root() is None


class TestLoadSessionRoots:
    def test_empty_when_neither_env_set(self, monkeypatch):
        monkeypatch.delenv(REPO_ROOT_ENV, raising=False)
        monkeypatch.delenv(PROJ_ROOT_ENV, raising=False)
        assert load_session_roots() == []

    def test_returns_repo_only_when_proj_unset(self, tmp_path, monkeypatch):
        repo = tmp_path / "repos"
        repo.mkdir()
        monkeypatch.setenv(REPO_ROOT_ENV, str(repo))
        monkeypatch.delenv(PROJ_ROOT_ENV, raising=False)
        assert load_session_roots() == [repo.resolve()]

    def test_returns_proj_only_when_repo_unset(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        monkeypatch.delenv(REPO_ROOT_ENV, raising=False)
        monkeypatch.setenv(PROJ_ROOT_ENV, str(proj))
        assert load_session_roots() == [proj.resolve()]

    def test_returns_both_in_repo_then_proj_order(self, tmp_path, monkeypatch):
        repo = tmp_path / "repos"
        repo.mkdir()
        proj = tmp_path / "proj"
        proj.mkdir()
        monkeypatch.setenv(REPO_ROOT_ENV, str(repo))
        monkeypatch.setenv(PROJ_ROOT_ENV, str(proj))
        assert load_session_roots() == [repo.resolve(), proj.resolve()]

    def test_dedups_when_repo_and_proj_point_to_same_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "shared"
        d.mkdir()
        monkeypatch.setenv(REPO_ROOT_ENV, str(d))
        monkeypatch.setenv(PROJ_ROOT_ENV, str(d))
        assert load_session_roots() == [d.resolve()]

    def test_skips_unconfigured_root_silently(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        # REPO points at a missing dir; should be silently skipped, not error.
        monkeypatch.setenv(REPO_ROOT_ENV, str(tmp_path / "no-such"))
        monkeypatch.setenv(PROJ_ROOT_ENV, str(proj))
        assert load_session_roots() == [proj.resolve()]


class TestIsStrictRoot:
    def test_false_when_proj_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv(PROJ_ROOT_ENV, raising=False)
        assert is_strict_root(tmp_path) is False

    def test_true_when_root_equals_proj_root(self, tmp_path, monkeypatch):
        d = tmp_path / "proj"
        d.mkdir()
        monkeypatch.setenv(PROJ_ROOT_ENV, str(d))
        assert is_strict_root(d.resolve()) is True

    def test_false_when_root_is_repo_root_not_proj(self, tmp_path, monkeypatch):
        repo = tmp_path / "repos"
        repo.mkdir()
        proj = tmp_path / "proj"
        proj.mkdir()
        monkeypatch.setenv(REPO_ROOT_ENV, str(repo))
        monkeypatch.setenv(PROJ_ROOT_ENV, str(proj))
        assert is_strict_root(repo.resolve()) is False
        assert is_strict_root(proj.resolve()) is True


class TestMatchedSessionRoot:
    def test_returns_root_for_direct_subdir(self, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        cwd = root / "p"
        cwd.mkdir()
        assert roots.matched_session_root(cwd, [root]) == root

    def test_returns_none_when_grandchild(self, tmp_path):
        root = tmp_path / "r"
        nested = root / "child" / "grand"
        nested.mkdir(parents=True)
        assert roots.matched_session_root(nested, [root]) is None

    def test_returns_none_when_outside_all_roots(self, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        elsewhere = tmp_path / "e"
        elsewhere.mkdir()
        assert roots.matched_session_root(elsewhere, [root]) is None
