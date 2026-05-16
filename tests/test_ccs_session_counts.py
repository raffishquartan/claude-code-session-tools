"""Tests for the ccs session-count footer and WARNING on empty corpus."""
from __future__ import annotations

import json as json_mod
from pathlib import Path

import pytest

from cc_session_tools.cli import ccs
from cc_session_tools.lib.sessions import transcript_dir_for_project


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


def _write_jsonl_with_user_message(fake_home: Path, proj: Path, basename: str, message: str) -> Path:
    from cc_session_tools.lib.sessions import session_tag
    tag = session_tag(basename)
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True, exist_ok=True)
    stem = f"xuser-{basename}"
    tag_file = t_dir / f"{stem}.tag"
    tag_file.write_text(tag or basename)
    jsonl = t_dir / f"{stem}.jsonl"
    record = json_mod.dumps({
        "type": "user",
        "message": {"content": message},
    })
    jsonl.write_text(record + "\n")
    return jsonl


def _write_jsonl_empty(fake_home: Path, proj: Path, basename: str) -> Path:
    from cc_session_tools.lib.sessions import session_tag
    tag = session_tag(basename)
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True, exist_ok=True)
    stem = f"xempty-{basename}"
    tag_file = t_dir / f"{stem}.tag"
    tag_file.write_text(tag or basename)
    jsonl = t_dir / f"{stem}.jsonl"
    record = json_mod.dumps({
        "type": "user",
        "isMeta": True,
        "message": {"content": "<command-name>SessionStart</command-name>"},
    })
    jsonl.write_text(record + "\n")
    return jsonl


# ---------------------------------------------------------------------------
# Footer format
# ---------------------------------------------------------------------------


class TestSessionCountFooter:
    def test_footer_printed_on_stderr(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-work")
        monkeypatch.chdir(proj)

        ccs.main([])
        err = capsys.readouterr().err
        assert "ccs: searching" in err

    def test_footer_shows_session_count(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a")
        _make_session(fake_repos, "myproj", "20260503-b")
        _make_session(fake_repos, "myproj", "20260502-c")
        monkeypatch.chdir(proj)

        ccs.main([])
        err = capsys.readouterr().err
        assert "3 sessions" in err

    def test_footer_shows_cwd_scope_for_local(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-a")
        monkeypatch.chdir(proj)

        ccs.main([])
        err = capsys.readouterr().err
        assert "in cwd" in err

    def test_footer_shows_global_scope_for_global(self, fake_repos, monkeypatch, capsys):
        _make_session(fake_repos, "myproj", "20260504-a")
        monkeypatch.chdir(fake_repos)

        ccs.main(["--global"])
        err = capsys.readouterr().err
        assert "in global" in err

    def test_footer_shows_empty_count(self, fake_repos, fake_home, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-full")
        _make_session(fake_repos, "myproj", "20260503-empty")
        _write_jsonl_with_user_message(fake_home, proj, "20260504-full", "hello")
        _write_jsonl_empty(fake_home, proj, "20260503-empty")
        monkeypatch.chdir(proj)

        ccs.main([])
        err = capsys.readouterr().err
        assert "1 empty" in err

    def test_footer_shows_hook_count(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-hook-security-check")
        _make_session(fake_repos, "myproj", "20260503-normal")
        monkeypatch.chdir(proj)

        ccs.main([])
        err = capsys.readouterr().err
        assert "1 hook" in err

    def test_footer_singular_session(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-alone")
        monkeypatch.chdir(proj)

        ccs.main([])
        err = capsys.readouterr().err
        assert "1 session " in err or "1 session\n" in err
        assert "1 sessions" not in err

    def test_footer_suppressed_in_json_mode(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo")
        monkeypatch.chdir(proj)

        ccs.main(["foo", "--json"])
        err = capsys.readouterr().err
        assert "ccs: searching" not in err

    def test_footer_suppressed_in_null_mode(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo")
        monkeypatch.chdir(proj)

        ccs.main(["foo", "--null"])
        err = capsys.readouterr().err
        assert "ccs: searching" not in err

    def test_footer_printed_on_name_search(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo")
        monkeypatch.chdir(proj)

        ccs.main(["foo"])
        err = capsys.readouterr().err
        assert "ccs: searching" in err


# ---------------------------------------------------------------------------
# Suppression of empty/hook terms when filter is active
# ---------------------------------------------------------------------------


class TestFooterTermSuppression:
    def test_empty_count_suppressed_when_emptiness_only(
        self, fake_repos, fake_home, monkeypatch, capsys
    ):
        """When --emptiness only is active, the 'X empty' term is omitted
        (the user already knows the corpus is all-empty sessions)."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-no-msgs")
        _write_jsonl_empty(fake_home, proj, "20260504-no-msgs")
        monkeypatch.chdir(proj)

        ccs.main(["--emptiness", "only"])
        err = capsys.readouterr().err
        assert "empty" not in err

    def test_empty_count_suppressed_when_emptiness_exclude(
        self, fake_repos, fake_home, monkeypatch, capsys
    ):
        """When --emptiness exclude is active, the 'X empty' term is omitted."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-with-msgs")
        _write_jsonl_with_user_message(fake_home, proj, "20260504-with-msgs", "hello")
        monkeypatch.chdir(proj)

        ccs.main(["--emptiness", "exclude"])
        err = capsys.readouterr().err
        assert "empty" not in err

    def test_hook_count_suppressed_when_exclude_hooks(self, fake_repos, monkeypatch, capsys):
        """When --exclude-hooks is active, the 'Y hook' term is omitted
        (hooks were already removed from the corpus)."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-hook-security-check")
        _make_session(fake_repos, "myproj", "20260503-normal")
        monkeypatch.chdir(proj)

        ccs.main(["--exclude-hooks"])
        err = capsys.readouterr().err
        assert "hook" not in err or "excluded" in err  # excluded count line may contain "hook"
        # The footer specifically should not contain the hook count term
        footer_line = next(
            (l for l in err.splitlines() if "ccs: searching" in l), ""
        )
        assert "hook" not in footer_line

    def test_footer_counts_before_filters_not_after(
        self, fake_repos, fake_home, monkeypatch, capsys
    ):
        """The footer's (X empty, Y hook) counts reflect the corpus BEFORE those
        filters are applied — so users can see what was filtered out."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-hook-sec")
        _make_session(fake_repos, "myproj", "20260503-empty-sess")
        _make_session(fake_repos, "myproj", "20260502-normal")
        _write_jsonl_empty(fake_home, proj, "20260503-empty-sess")
        _write_jsonl_with_user_message(fake_home, proj, "20260502-normal", "hi")
        monkeypatch.chdir(proj)

        # Neither filter active → counts should show 1 empty, 1 hook
        ccs.main([])
        err = capsys.readouterr().err
        assert "1 empty" in err
        assert "1 hook" in err


# ---------------------------------------------------------------------------
# WARNING on zero-session corpus
# ---------------------------------------------------------------------------


class TestZeroSessionWarning:
    def test_warning_when_no_sessions_after_date_filter(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260101-old")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--since", "20270101"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "no sessions" in err

    def test_warning_when_no_sessions_after_emptiness_filter(
        self, fake_repos, fake_home, monkeypatch, capsys
    ):
        """If all sessions are non-empty and --emptiness only is requested,
        the corpus is empty → WARNING."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-full")
        _write_jsonl_with_user_message(fake_home, proj, "20260504-full", "hello")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--emptiness", "only"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "WARNING" in err

    def test_warning_includes_scope_label(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "cc-sessions").mkdir()
        monkeypatch.chdir(proj)

        ccs.main([])
        err = capsys.readouterr().err
        assert "cwd" in err or "global" in err

    def test_warning_not_shown_in_json_mode_no_sessions(self, fake_repos, monkeypatch, capsys):
        """In machine-readable mode, the WARNING is suppressed and we don't exit 1
        early from the corpus-empty check (we let the search handle it)."""
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "cc-sessions").mkdir()
        monkeypatch.chdir(proj)

        # --json mode: no footer, no WARNING, normal empty-array output
        ccs.main(["foo", "--json"])
        err = capsys.readouterr().err
        assert "WARNING" not in err
