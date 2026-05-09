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


class TestContentsSearchHeader:
    def test_header_prints_count_immediately_before_any_size_walk(
        self, fake_repos, monkeypatch, capsys
    ):
        # Make _session_size raise to prove the header is emitted *before* any
        # filesystem walking - if the header still appears, the count-only
        # header is doing its job and the size pre-pass is gone.
        from cc_session_tools.cli import ccs as ccs_mod

        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="alpha\n")
        _make_session(fake_repos, "myproj", "20260503-b", contents="beta\n")
        monkeypatch.chdir(proj)

        def boom(_):
            raise AssertionError(
                "ccs walked file sizes upfront - header must print before any rglob"
            )

        monkeypatch.setattr(ccs_mod, "_session_size", boom)

        with pytest.raises(AssertionError, match="walked file sizes upfront"):
            ccs.main(["needle", "--contents"])

        err = capsys.readouterr().err
        assert "2 sessions" in err  # header printed first

    def test_header_singular_session_phrasing(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-only", contents="x\n")
        monkeypatch.chdir(proj)

        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        assert "1 session" in err
        # No accidental plural.
        assert "1 sessions" not in err

    def test_header_not_printed_for_name_search(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo", contents="x\n")
        monkeypatch.chdir(proj)

        ccs.main(["foo"])  # name search, no --contents
        err = capsys.readouterr().err
        assert "sessions" not in err

    def test_header_global_aggregates_across_roots(self, fake_repos, monkeypatch, capsys):
        _make_session(fake_repos, "alpha", "20260504-a", contents="x\n")
        _make_session(fake_repos, "beta", "20260503-b", contents="y\n")
        monkeypatch.chdir(fake_repos)

        ccs.main(["needle", "--contents", "--global"])
        err = capsys.readouterr().err
        assert "2 sessions" in err


class TestContentsSearchSummary:
    def test_summary_reports_files_and_size_after_search(
        self, fake_repos, monkeypatch, capsys
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="alpha\n" * 100)
        _make_session(fake_repos, "myproj", "20260503-b", contents="beta\n" * 50)
        monkeypatch.chdir(proj)

        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        # Final summary line shows files searched and total size.
        assert "files" in err
        assert any(unit in err for unit in (" B", " KB", " MB"))

    def test_no_summary_for_name_search(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo", contents="x\n")
        monkeypatch.chdir(proj)

        ccs.main(["foo"])
        err = capsys.readouterr().err
        assert "files" not in err
        assert " KB" not in err
        assert " MB" not in err


class TestContentsSearchProgress:
    def test_progress_emitted_when_stderr_is_a_tty(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="x\n")
        _make_session(fake_repos, "myproj", "20260503-b", contents="y\n")
        monkeypatch.chdir(proj)

        # Pretend stderr is a TTY so progress fires.
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)

        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        # Progress lines use carriage returns to update in place.
        assert "\r" in err
        # Some indication of progress (e.g. "1/2", "2/2") should appear.
        assert "/2" in err

    def test_progress_silent_when_stderr_not_a_tty(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a", contents="x\n")
        monkeypatch.chdir(proj)

        # Default in pytest: stderr is not a TTY. Header still prints; progress does not.
        ccs.main(["needle", "--contents"])
        err = capsys.readouterr().err
        assert "\r" not in err
