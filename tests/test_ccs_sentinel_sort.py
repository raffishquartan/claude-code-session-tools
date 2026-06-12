"""Tests for ccs sentinel-based sorting (--order-by opened / --order-by active)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from cc_session_tools.cli import ccs
from cc_session_tools.cli.ccs import (
    _Result,
    _get_sentinel_mtime,
    _sort_results,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def fake_repos(fake_home, tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    repos.mkdir()
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos))
    return repos


def _make_session(repos: Path, project: str, basename: str) -> Path:
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    return sess


# ---------------------------------------------------------------------------
# _get_sentinel_mtime
# ---------------------------------------------------------------------------

class TestGetSentinelMtime:
    def test_returns_mtime_when_file_exists(self, tmp_path):
        sentinel = tmp_path / ".last-opened"
        sentinel.touch()
        result = _get_sentinel_mtime(tmp_path, ".last-opened")
        assert result > 0.0
        assert abs(result - sentinel.stat().st_mtime) < 1.0

    def test_returns_zero_when_file_absent(self, tmp_path):
        result = _get_sentinel_mtime(tmp_path, ".last-opened")
        assert result == 0.0

    def test_returns_zero_on_oserror(self, tmp_path):
        # Pass a file as the directory — stat on file/.last-opened will fail
        not_a_dir = tmp_path / "not_a_dir"
        not_a_dir.write_text("content")
        result = _get_sentinel_mtime(not_a_dir, ".last-opened")
        assert result == 0.0


# ---------------------------------------------------------------------------
# _sort_results with order_by opened / active
# ---------------------------------------------------------------------------

class TestSortResultsSentinel:
    def _make_result(self, basename: str, project_dir: Path) -> _Result:
        return _Result(
            date_key="20260612",
            basename=basename,
            project_dir=project_dir,
            context_lines=[],
        )

    def test_sort_opened_session_with_sentinel_sorts_above_without(self, tmp_path):
        r1 = self._make_result("20260612-no-sentinel", tmp_path)
        r1.opened_mtime = 0.0

        r2 = self._make_result("20260612-with-sentinel", tmp_path)
        r2.opened_mtime = time.time()

        results = [r1, r2]
        sorted_results = _sort_results(results, "datetime", order_by="opened")
        assert sorted_results[0].basename == "20260612-with-sentinel"
        assert sorted_results[1].basename == "20260612-no-sentinel"

    def test_sort_active_session_with_sentinel_sorts_above_without(self, tmp_path):
        r1 = self._make_result("20260612-no-sentinel", tmp_path)
        r1.active_mtime = 0.0

        r2 = self._make_result("20260612-with-sentinel", tmp_path)
        r2.active_mtime = time.time()

        results = [r1, r2]
        sorted_results = _sort_results(results, "datetime", order_by="active")
        assert sorted_results[0].basename == "20260612-with-sentinel"
        assert sorted_results[1].basename == "20260612-no-sentinel"

    def test_sort_opened_most_recent_first(self, tmp_path):
        now = time.time()
        r1 = self._make_result("20260612-older", tmp_path)
        r1.opened_mtime = now - 3600

        r2 = self._make_result("20260612-newer", tmp_path)
        r2.opened_mtime = now

        results = [r1, r2]
        sorted_results = _sort_results(results, "datetime", order_by="opened")
        assert sorted_results[0].basename == "20260612-newer"

    def test_sort_active_most_recent_first(self, tmp_path):
        now = time.time()
        r1 = self._make_result("20260612-older", tmp_path)
        r1.active_mtime = now - 3600

        r2 = self._make_result("20260612-newer", tmp_path)
        r2.active_mtime = now

        results = [r1, r2]
        sorted_results = _sort_results(results, "datetime", order_by="active")
        assert sorted_results[0].basename == "20260612-newer"


# ---------------------------------------------------------------------------
# List mode output with order_by opened / active
# ---------------------------------------------------------------------------

class TestListModeSentinelOutput:
    def test_list_mode_opened_includes_label_and_timestamp(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        sess = _make_session(fake_repos, "myproj", "20260612-a-sess")
        # Create the sentinel
        (sess / ".last-opened").touch()
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "opened:" in out
        assert "20260612-a-sess" in out

    def test_list_mode_opened_shows_never_when_no_sentinel(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-no-sentinel")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "(never)" in out

    def test_list_mode_active_includes_label_and_timestamp(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        sess = _make_session(fake_repos, "myproj", "20260612-b-sess")
        (sess / ".last-active").touch()
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "active"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "active:" in out
        assert "20260612-b-sess" in out

    def test_list_mode_active_shows_never_when_no_sentinel(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-no-active")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "active"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "(never)" in out

    def test_order_by_opened_accepted_by_argparser(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-argstest")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "opened"])
        assert rc == 0

    def test_order_by_active_accepted_by_argparser(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-argstest2")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--order-by", "active"])
        assert rc == 0

    def test_list_mode_opened_global_includes_proj_path(self, fake_repos, monkeypatch, capsys):
        sess = _make_session(fake_repos, "alpha", "20260612-alpha-sess")
        (sess / ".last-opened").touch()
        monkeypatch.chdir(fake_repos)

        rc = ccs.main(["--global", "--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "opened:" in out
        assert "alpha" in out


# ---------------------------------------------------------------------------
# Search mode output with order_by opened / active
# ---------------------------------------------------------------------------

class TestSearchModeSentinelOutput:
    def test_search_mode_opened_includes_label(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        sess = _make_session(fake_repos, "myproj", "20260612-search-opened")
        (sess / ".last-opened").touch()
        monkeypatch.chdir(proj)

        rc = ccs.main(["search-opened", "--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "opened:" in out

    def test_search_mode_active_includes_label(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        sess = _make_session(fake_repos, "myproj", "20260612-search-active")
        (sess / ".last-active").touch()
        monkeypatch.chdir(proj)

        rc = ccs.main(["search-active", "--order-by", "active"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "active:" in out

    def test_search_mode_opened_never_when_no_sentinel(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260612-search-no-sentinel")
        monkeypatch.chdir(proj)

        rc = ccs.main(["search-no-sentinel", "--order-by", "opened"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "(never)" in out
