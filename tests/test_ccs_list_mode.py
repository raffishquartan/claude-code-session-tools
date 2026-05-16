"""Tests for ccs list mode (no positional query, no --name/--contents/--messages)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.cli import ccs


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


def _make_session(repos: Path, project: str, basename: str, *, contents: str | None = None) -> Path:
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    if contents is not None:
        (sess / "working" / "WORKLOG.md").write_text(contents)
    return sess


# ---------------------------------------------------------------------------
# Basic list mode behaviour
# ---------------------------------------------------------------------------


class TestListModeBasic:
    def test_no_args_lists_all_sessions(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo-bar")
        _make_session(fake_repos, "myproj", "20260503-baz-qux")
        monkeypatch.chdir(proj)

        rc = ccs.main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "20260504-foo-bar" in out
        assert "20260503-baz-qux" in out

    def test_list_mode_newest_first(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260101-old")
        _make_session(fake_repos, "myproj", "20260504-new")
        _make_session(fake_repos, "myproj", "20260301-mid")
        monkeypatch.chdir(proj)

        rc = ccs.main([])
        assert rc == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[0] == "20260504-new"
        assert lines[1] == "20260301-mid"
        assert lines[2] == "20260101-old"

    def test_list_mode_exits_0_when_sessions_exist(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-anything")
        monkeypatch.chdir(proj)

        rc = ccs.main([])
        assert rc == 0

    def test_list_mode_exits_1_when_no_sessions(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "cc-sessions").mkdir()
        monkeypatch.chdir(proj)

        rc = ccs.main([])
        assert rc == 1

    def test_list_mode_warning_on_stderr_when_no_sessions(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "cc-sessions").mkdir()
        monkeypatch.chdir(proj)

        ccs.main([])
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "no sessions" in err

    def test_list_mode_one_line_per_session(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a")
        _make_session(fake_repos, "myproj", "20260503-b")
        _make_session(fake_repos, "myproj", "20260502-c")
        monkeypatch.chdir(proj)

        rc = ccs.main([])
        assert rc == 0
        lines = [l for l in capsys.readouterr().out.strip().splitlines() if l]
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# List mode with --global
# ---------------------------------------------------------------------------


class TestListModeGlobal:
    def test_global_list_includes_all_projects(self, fake_repos, monkeypatch, capsys):
        _make_session(fake_repos, "alpha", "20260504-sess-a")
        _make_session(fake_repos, "beta", "20260503-sess-b")
        monkeypatch.chdir(fake_repos)

        rc = ccs.main(["--global"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "20260504-sess-a" in out
        assert "20260503-sess-b" in out

    def test_global_list_includes_project_path(self, fake_repos, monkeypatch, capsys):
        _make_session(fake_repos, "alpha", "20260504-sess-a")
        monkeypatch.chdir(fake_repos)

        rc = ccs.main(["--global"])
        assert rc == 0
        out = capsys.readouterr().out
        # In global mode, the project directory path is shown in parens
        assert "(" in out and ")" in out

    def test_local_list_excludes_other_projects(self, fake_repos, monkeypatch, capsys):
        _make_session(fake_repos, "alpha", "20260504-alpha-sess")
        _make_session(fake_repos, "beta", "20260503-beta-sess")
        proj_alpha = fake_repos / "alpha"
        monkeypatch.chdir(proj_alpha)
        monkeypatch.setenv("CCS_DEFAULT_GLOBAL", "1")

        rc = ccs.main(["--local"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "alpha-sess" in out
        assert "beta-sess" not in out


# ---------------------------------------------------------------------------
# List mode with filters
# ---------------------------------------------------------------------------


class TestListModeWithFilters:
    def test_list_mode_respects_since_filter(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260101-old")
        _make_session(fake_repos, "myproj", "20260504-new")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--since", "20260301"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "20260504-new" in out
        assert "20260101-old" not in out

    def test_list_mode_respects_exclude_hooks(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-hook-security-check")
        _make_session(fake_repos, "myproj", "20260503-real-work")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--exclude-hooks"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "real-work" in out
        assert "hook-security-check" not in out

    def test_list_mode_sort_alpha(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-zzz-last")
        _make_session(fake_repos, "myproj", "20260101-aaa-first")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--sort", "alpha"])
        assert rc == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[0].startswith("20260101-aaa")
        assert lines[1].startswith("20260504-zzz")


# ---------------------------------------------------------------------------
# List mode vs search mode disambiguation
# ---------------------------------------------------------------------------


class TestListModeVsSearchMode:
    def test_positional_query_enters_name_search_not_list(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo-bar")
        _make_session(fake_repos, "myproj", "20260503-unrelated")
        monkeypatch.chdir(proj)

        rc = ccs.main(["foo"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "foo-bar" in out
        assert "unrelated" not in out  # name search, not list mode

    def test_name_flag_alone_is_not_list_mode(self, fake_repos, monkeypatch, capsys):
        """--name with no value and no positional → error (missing query), not list."""
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(proj)

        rc = ccs.main(["--name"])
        assert rc == 1

    def test_contents_flag_alone_is_not_list_mode(self, fake_repos, monkeypatch, capsys):
        """--contents with no value and no positional → error (missing query), not list."""
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(proj)

        rc = ccs.main(["--contents"])
        assert rc == 1


# ---------------------------------------------------------------------------
# List mode with no cc-sessions directory
# ---------------------------------------------------------------------------


class TestListModeNoCcSessions:
    def test_no_cc_sessions_dir_exits_1(self, fake_repos, monkeypatch, capsys):
        elsewhere = fake_repos / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        rc = ccs.main([])
        assert rc == 1
        err = capsys.readouterr().err
        assert "cc-sessions" in err
