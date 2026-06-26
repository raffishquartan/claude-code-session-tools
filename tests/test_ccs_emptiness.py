"""Tests for ccs --emptiness {only,exclude,any} filter flag."""
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
    # Point CCCS_SESSION_TAGS_DIR to a flat tags dir so _session_tags_dir()
    # resolves here rather than ~/.cache/claude/session-tags/.
    tags_dir = tmp_path / "session-tags"
    tags_dir.mkdir()
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tags_dir))
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
    """Write a JSONL transcript for basename with one real user message."""
    import os
    from cc_session_tools.lib.sessions import session_tag, _session_tags_dir
    tag = session_tag(basename)
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True, exist_ok=True)
    stem = "abc-user-msg"
    # Write tag file to the flat tags dir (respects CCCS_SESSION_TAGS_DIR).
    tags_dir = _session_tags_dir()
    tags_dir.mkdir(parents=True, exist_ok=True)
    (tags_dir / f"{stem}.tag").write_text(tag or basename)
    jsonl = t_dir / f"{stem}.jsonl"
    record = json_mod.dumps({
        "type": "user",
        "message": {"content": message},
    })
    jsonl.write_text(record + "\n")
    return jsonl


def _write_jsonl_empty(fake_home: Path, proj: Path, basename: str) -> Path:
    """Write a JSONL transcript for basename with NO real user messages (only hook output)."""
    from cc_session_tools.lib.sessions import session_tag, _session_tags_dir
    tag = session_tag(basename)
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True, exist_ok=True)
    stem = "abc-empty"
    # Write tag file to the flat tags dir (respects CCCS_SESSION_TAGS_DIR).
    tags_dir = _session_tags_dir()
    tags_dir.mkdir(parents=True, exist_ok=True)
    (tags_dir / f"{stem}.tag").write_text(tag or basename)
    jsonl = t_dir / f"{stem}.jsonl"
    # Only a SessionStart hook message (isMeta=True) — no user typed content.
    record = json_mod.dumps({
        "type": "user",
        "isMeta": True,
        "message": {"content": "<command-name>SessionStart</command-name>"},
    })
    jsonl.write_text(record + "\n")
    return jsonl


# ---------------------------------------------------------------------------
# --emptiness any (default)
# ---------------------------------------------------------------------------


class TestEmptinessAny:
    def test_default_any_includes_all_sessions(self, fake_repos, fake_home, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-with-msgs")
        _make_session(fake_repos, "myproj", "20260503-no-msgs")
        _write_jsonl_with_user_message(fake_home, proj, "20260504-with-msgs", "hello world")
        _write_jsonl_empty(fake_home, proj, "20260503-no-msgs")
        monkeypatch.chdir(proj)

        rc = ccs.main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "with-msgs" in out
        assert "no-msgs" in out

    def test_explicit_any_matches_default(self, fake_repos, fake_home, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-with-msgs")
        _make_session(fake_repos, "myproj", "20260503-no-msgs")
        _write_jsonl_with_user_message(fake_home, proj, "20260504-with-msgs", "hello world")
        _write_jsonl_empty(fake_home, proj, "20260503-no-msgs")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--emptiness", "any"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "with-msgs" in out
        assert "no-msgs" in out


# ---------------------------------------------------------------------------
# --emptiness only
# ---------------------------------------------------------------------------


class TestEmptinessOnly:
    def test_only_shows_empty_sessions(self, fake_repos, fake_home, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-with-msgs")
        _make_session(fake_repos, "myproj", "20260503-no-msgs")
        _write_jsonl_with_user_message(fake_home, proj, "20260504-with-msgs", "hello world")
        _write_jsonl_empty(fake_home, proj, "20260503-no-msgs")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--emptiness", "only"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "no-msgs" in out
        assert "with-msgs" not in out

    def test_only_exits_1_when_no_empty_sessions(self, fake_repos, fake_home, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-with-msgs")
        _write_jsonl_with_user_message(fake_home, proj, "20260504-with-msgs", "hello world")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--emptiness", "only"])
        assert rc == 1

    def test_only_works_with_name_search(self, fake_repos, fake_home, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-target-full")
        _make_session(fake_repos, "myproj", "20260503-target-empty")
        _write_jsonl_with_user_message(fake_home, proj, "20260504-target-full", "hello world")
        _write_jsonl_empty(fake_home, proj, "20260503-target-empty")
        monkeypatch.chdir(proj)

        rc = ccs.main(["target", "--emptiness", "only"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "target-empty" in out
        assert "target-full" not in out

    def test_unknown_jsonl_session_treated_as_non_empty(self, fake_repos, fake_home, monkeypatch, capsys):
        """Sessions with no JSONL (unknown emptiness) are treated as non-empty
        under --emptiness only so they don't get incorrectly tagged as empty."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-no-jsonl")
        # No JSONL written → session_is_empty_safe returns None
        monkeypatch.chdir(proj)

        rc = ccs.main(["--emptiness", "only"])
        # Should exit 1: the unknown-JSONL session is not shown as "empty"
        assert rc == 1
        out = capsys.readouterr().out
        assert "no-jsonl" not in out


# ---------------------------------------------------------------------------
# --emptiness exclude
# ---------------------------------------------------------------------------


class TestEmptinessExclude:
    def test_exclude_hides_empty_sessions(self, fake_repos, fake_home, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-with-msgs")
        _make_session(fake_repos, "myproj", "20260503-no-msgs")
        _write_jsonl_with_user_message(fake_home, proj, "20260504-with-msgs", "hello world")
        _write_jsonl_empty(fake_home, proj, "20260503-no-msgs")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--emptiness", "exclude"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "with-msgs" in out
        assert "no-msgs" not in out

    def test_exclude_shows_unknown_jsonl_sessions(self, fake_repos, fake_home, monkeypatch, capsys):
        """Sessions with no JSONL (treated as non-empty) appear under --emptiness exclude."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-no-jsonl")
        # No JSONL written → session_is_empty_safe returns None → treated as non-empty
        monkeypatch.chdir(proj)

        rc = ccs.main(["--emptiness", "exclude"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "no-jsonl" in out

    def test_exclude_exits_1_when_all_empty(self, fake_repos, fake_home, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-no-msgs")
        _write_jsonl_empty(fake_home, proj, "20260504-no-msgs")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--emptiness", "exclude"])
        assert rc == 1


# ---------------------------------------------------------------------------
# --emptiness in combination with list mode vs name search
# ---------------------------------------------------------------------------


class TestEmptinessCombinations:
    def test_emptiness_with_name_search_filters_before_search(
        self, fake_repos, fake_home, monkeypatch, capsys
    ):
        """The emptiness filter applies before search, so sessions filtered out
        don't appear even if they match the query."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-alpha-full")
        _make_session(fake_repos, "myproj", "20260503-alpha-empty")
        _write_jsonl_with_user_message(fake_home, proj, "20260504-alpha-full", "hello")
        _write_jsonl_empty(fake_home, proj, "20260503-alpha-empty")
        monkeypatch.chdir(proj)

        # Search for "alpha" with exclude → only the non-empty one
        rc = ccs.main(["alpha", "--emptiness", "exclude"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "alpha-full" in out
        assert "alpha-empty" not in out

    def test_emptiness_only_flag_is_mutually_exclusive_with_any(
        self, fake_repos, monkeypatch, capsys
    ):
        """argparse enforces choices; passing an invalid value exits with error."""
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(proj)

        with pytest.raises(SystemExit) as exc_info:
            ccs.main(["--emptiness", "bogus"])
        assert exc_info.value.code != 0
