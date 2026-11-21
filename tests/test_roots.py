from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_session_tools.lib import roots
from cc_session_tools.lib.roots import (
    PROJ_ROOT_ENV,
    REPO_ROOT_ENV,
    RootsConfigError,
    covering_entry,
    is_strict_root,
    load_session_roots,
    proj_root,
    repo_root,
    uncovered_session_roots,
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

    def test_load_raises_when_repo_env_set_to_missing_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv(REPO_ROOT_ENV, str(tmp_path / "no-such-dir"))
        with pytest.raises(RootsConfigError) as exc_info:
            load_session_roots()
        msg = str(exc_info.value)
        assert "[CST-ROOTS-CONFIG-ERROR]" in msg
        assert REPO_ROOT_ENV in msg
        assert "does not exist" in msg

    def test_returns_none_when_path_is_a_file(self, tmp_path, monkeypatch):
        f = tmp_path / "not-a-dir"
        f.write_text("hi")
        monkeypatch.setenv(REPO_ROOT_ENV, str(f))
        assert repo_root() is None

    def test_load_raises_when_repo_env_set_to_file(self, tmp_path, monkeypatch):
        f = tmp_path / "not-a-dir"
        f.write_text("hi")
        monkeypatch.setenv(REPO_ROOT_ENV, str(f))
        with pytest.raises(RootsConfigError) as exc_info:
            load_session_roots()
        msg = str(exc_info.value)
        assert "[CST-ROOTS-CONFIG-ERROR]" in msg
        assert REPO_ROOT_ENV in msg
        assert "file" in msg

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
    def test_raises_when_neither_env_set(self, monkeypatch):
        monkeypatch.delenv(REPO_ROOT_ENV, raising=False)
        monkeypatch.delenv(PROJ_ROOT_ENV, raising=False)
        with pytest.raises(RootsConfigError) as exc_info:
            load_session_roots()
        assert "[CST-ROOTS-CONFIG-ERROR]" in str(exc_info.value)

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

    def test_raises_when_one_good_one_bad_env(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        # PROJ is valid; REPO points at a missing path → must raise, not silently use PROJ only.
        monkeypatch.setenv(REPO_ROOT_ENV, str(tmp_path / "no-such"))
        monkeypatch.setenv(PROJ_ROOT_ENV, str(proj))
        with pytest.raises(RootsConfigError) as exc_info:
            load_session_roots()
        msg = str(exc_info.value)
        assert "[CST-ROOTS-CONFIG-ERROR]" in msg
        assert REPO_ROOT_ENV in msg


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


def _settings(*dirs: object) -> dict[str, object]:
    return {"permissions": {"additionalDirectories": list(dirs)}}


class TestCoveringEntry:
    def test_returns_the_ancestor_entry(self, tmp_path):
        root = tmp_path / "home" / "repos"
        root.mkdir(parents=True)
        entry = str(tmp_path / "home")
        assert covering_entry(root, _settings(entry)) == entry

    def test_returns_entry_equal_to_root(self, tmp_path):
        root = tmp_path / "repos"
        root.mkdir()
        assert covering_entry(root, _settings(str(root))) == str(root)

    def test_expands_tilde(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        root = tmp_path / "repos"
        root.mkdir()
        assert covering_entry(root, _settings("~")) == "~"

    def test_resolves_a_symlinked_root(self, tmp_path):
        storage = tmp_path / "home" / "storage" / "claude"
        storage.mkdir(parents=True)
        link = tmp_path / "outside" / "cc"
        link.parent.mkdir()
        link.symlink_to(storage)
        entry = str(tmp_path / "home")
        assert covering_entry(link, _settings(entry)) == entry

    def test_none_when_uncovered(self, tmp_path):
        root = tmp_path / "repos"
        root.mkdir()
        other = tmp_path / "elsewhere"
        other.mkdir()
        assert covering_entry(root, _settings(str(other))) is None

    def test_does_not_treat_a_sibling_with_a_shared_prefix_as_covering(self, tmp_path):
        root = tmp_path / "repos"
        root.mkdir()
        assert covering_entry(root, _settings(str(tmp_path / "rep"))) is None

    @pytest.mark.parametrize("value", ["/", None, 3, {"/": 1}])
    def test_none_when_value_is_not_a_list(self, tmp_path, value):
        root = tmp_path / "repos"
        root.mkdir()
        assert covering_entry(root, {"permissions": {"additionalDirectories": value}}) is None

    def test_none_when_key_is_absent(self, tmp_path):
        root = tmp_path / "repos"
        root.mkdir()
        assert covering_entry(root, {"hooks": {}}) is None

    def test_ignores_non_string_entries(self, tmp_path):
        root = tmp_path / "repos"
        root.mkdir()
        assert covering_entry(root, _settings(None, 3)) is None

    @pytest.mark.parametrize("entry", ["", ".", "repos", "../"])
    def test_ignores_relative_entries(self, tmp_path, monkeypatch, entry):
        root = tmp_path / "repos"
        root.mkdir()
        monkeypatch.chdir(tmp_path)
        assert covering_entry(root, _settings(entry)) is None

    def test_compares_entries_that_do_not_exist_lexically(self, tmp_path):
        root = tmp_path / "future" / "repos"
        root.mkdir(parents=True)
        entry = str(tmp_path / "future")
        assert covering_entry(root, _settings(entry)) == entry
        assert covering_entry(root, _settings(str(tmp_path / "nope"))) is None


class TestUncoveredSessionRoots:
    def _roots(self, tmp_path, monkeypatch):
        repos = tmp_path / "repos"
        proj = tmp_path / "cc"
        repos.mkdir()
        proj.mkdir()
        monkeypatch.setenv(REPO_ROOT_ENV, str(repos))
        monkeypatch.setenv(PROJ_ROOT_ENV, str(proj))
        return repos.resolve(), proj.resolve()

    def _write(self, tmp_path, data):
        path = tmp_path / "settings.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_both_uncovered(self, tmp_path, monkeypatch):
        repos, proj = self._roots(tmp_path, monkeypatch)
        settings = self._write(tmp_path, _settings())
        assert uncovered_session_roots(settings) == [repos, proj]

    def test_only_the_uncovered_root_is_returned(self, tmp_path, monkeypatch):
        repos, proj = self._roots(tmp_path, monkeypatch)
        settings = self._write(tmp_path, _settings(str(repos)))
        assert uncovered_session_roots(settings) == [proj]

    def test_empty_when_all_covered(self, tmp_path, monkeypatch):
        self._roots(tmp_path, monkeypatch)
        settings = self._write(tmp_path, _settings(str(tmp_path)))
        assert uncovered_session_roots(settings) == []

    def test_dedups_a_root_set_twice(self, tmp_path, monkeypatch):
        repos, _ = self._roots(tmp_path, monkeypatch)
        monkeypatch.setenv(PROJ_ROOT_ENV, str(repos))
        settings = self._write(tmp_path, _settings())
        assert uncovered_session_roots(settings) == [repos]

    @pytest.mark.parametrize("text", [None, "{not json", "[]", '"x"'])
    def test_empty_when_settings_missing_or_not_an_object(self, tmp_path, monkeypatch, text):
        self._roots(tmp_path, monkeypatch)
        path = tmp_path / "settings.json"
        if text is not None:
            path.write_text(text)
        assert uncovered_session_roots(path) == []

    def test_empty_when_no_roots_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv(REPO_ROOT_ENV, raising=False)
        monkeypatch.delenv(PROJ_ROOT_ENV, raising=False)
        assert uncovered_session_roots(self._write(tmp_path, _settings())) == []

    def test_a_missing_root_directory_is_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setenv(REPO_ROOT_ENV, str(tmp_path / "nope"))
        monkeypatch.delenv(PROJ_ROOT_ENV, raising=False)
        assert uncovered_session_roots(self._write(tmp_path, _settings())) == []
