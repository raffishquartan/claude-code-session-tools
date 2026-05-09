from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.cli import ccs


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


def test_name_search_in_current_dir_lists_matches(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo-bar")
    _make_session(fake_repos, "myproj", "20260503-other")
    monkeypatch.chdir(proj)

    rc = ccs.main(["foo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-foo-bar" in out
    assert "20260503-other" not in out


def test_name_search_orders_descending_by_date(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260101-foo-old")
    _make_session(fake_repos, "myproj", "20260504-foo-new")
    _make_session(fake_repos, "myproj", "20260301-foo-mid")
    monkeypatch.chdir(proj)

    rc = ccs.main(["foo"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines == [
        "20260504-foo-new",
        "20260301-foo-mid",
        "20260101-foo-old",
    ]


def test_name_search_no_match_returns_1(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo")
    monkeypatch.chdir(proj)

    rc = ccs.main(["nope"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no sessions match" in err


def test_global_name_search_includes_project_path(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "alpha", "20260504-foo")
    _make_session(fake_repos, "beta", "20260503-foo")
    monkeypatch.chdir(fake_repos)  # cwd doesn't matter for --global

    rc = ccs.main(["foo", "--global"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-foo" in out
    assert "20260503-foo" in out
    assert str(fake_repos / "alpha") in out
    assert str(fake_repos / "beta") in out


def test_contents_search_finds_match_with_context(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(
        fake_repos, "myproj", "20260504-eats",
        contents=(
            "The plan is as follows:\n"
            "  - Eat lots of flambe\n"
            "  - Dance\n"
        ),
    )
    monkeypatch.chdir(proj)

    rc = ccs.main(["flambe", "--contents"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-eats" in out
    assert "The plan is as follows" in out
    assert "Eat lots of flambe" in out
    assert "Dance" in out


def test_contents_search_no_match_returns_1(fake_repos, monkeypatch, capsys):
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-foo", contents="nothing relevant\n")
    monkeypatch.chdir(proj)

    rc = ccs.main(["flambe", "--contents"])
    assert rc == 1


def test_global_contents_search_shows_project_path(fake_repos, monkeypatch, capsys):
    _make_session(
        fake_repos, "alpha", "20260504-foo",
        contents="alpha line\nflambe here\nafter\n",
    )
    monkeypatch.chdir(fake_repos)

    rc = ccs.main(["flambe", "--contents", "--global"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "20260504-foo" in out
    assert str(fake_repos / "alpha") in out
    assert "flambe here" in out


def test_no_cc_sessions_in_cwd_errors_for_local_search(fake_repos, monkeypatch, capsys, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    rc = ccs.main(["foo"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no cc-sessions" in err
