"""Tests for ccs scope flags (--name, --contents, --messages),
extended --since ISO format, and --sort.

TDD order: each test class/function is implemented one at a time following
strict Red-Green-Refactor.
"""
from __future__ import annotations

import json as json_mod
from pathlib import Path
from typing import Any

import pytest

from cc_session_tools.cli import ccs
from cc_session_tools.lib.sessions import transcript_dir_for_project


# ---------------------------------------------------------------------------
# Fixtures (shared across all test groups in this file)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))
    return home


@pytest.fixture
def fake_repos(fake_home, tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    repos.mkdir()
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos))
    return repos


@pytest.fixture
def force_grep_path(monkeypatch):
    """Force ccs to use the grep fallback by suppressing rg detection."""
    from cc_session_tools.cli import ccs as ccs_mod

    real_which = ccs_mod.shutil.which

    def fake_which(name, *args, **kwargs):
        if name == "rg":
            return None
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(ccs_mod.shutil, "which", fake_which)


def _make_session(
    repos: Path, project: str, basename: str, *, contents: str | None = None
) -> Path:
    from cc_session_tools.lib import sessions_db
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    if contents is not None:
        (sess / "working" / "WORKLOG.md").write_text(contents)
    sessions_db.ensure_session_row(repos / project, basename)
    return sess


def _make_transcript(
    fake_home: Path, proj: Path, filename: str, text: str
) -> Path:
    """Write a JSONL-like file into the transcript dir for proj."""
    t_dir = transcript_dir_for_project(proj)
    t_dir.mkdir(parents=True, exist_ok=True)
    f = t_dir / filename
    f.write_text(text)
    return f


# ---------------------------------------------------------------------------
# Test 1: --name with positional gives same results as bare ccs <q>
# ---------------------------------------------------------------------------


class TestNameScope:
    def test_name_flag_with_positional_matches_bare_query(
        self, fake_repos, monkeypatch, capsys
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo-bar")
        _make_session(fake_repos, "myproj", "20260503-other")
        monkeypatch.chdir(proj)

        rc_bare = ccs.main(["foo"])
        out_bare = capsys.readouterr().out

        rc_flag = ccs.main(["foo", "--name"])
        out_flag = capsys.readouterr().out

        assert rc_bare == 0
        assert rc_flag == 0
        assert out_bare == out_flag

    def test_name_flag_value_overrides_positional_for_name_scope(
        self, fake_repos, monkeypatch, capsys
    ):
        """--name "bar" should search for "bar", not the positional "foo"."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-bar-session")
        _make_session(fake_repos, "myproj", "20260503-foo-session")
        monkeypatch.chdir(proj)

        rc = ccs.main(["foo", "--name", "bar"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "bar-session" in out
        assert "foo-session" not in out

    def test_name_flag_without_positional_uses_flag_value(
        self, fake_repos, monkeypatch, capsys
    ):
        """ccs --name "foo" (no positional) should search for "foo"."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo-session")
        _make_session(fake_repos, "myproj", "20260503-other")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--name", "foo"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "foo-session" in out
        assert "other" not in out


# ---------------------------------------------------------------------------
# Test 2: --messages searches only transcripts, not working files
# ---------------------------------------------------------------------------


class TestMessagesScope:
    def test_messages_searches_transcript_not_working(
        self, fake_repos, fake_home, monkeypatch, capsys, force_grep_path
    ):
        proj = fake_repos / "myproj"
        _make_session(
            fake_repos, "myproj", "20260504-foo",
            contents="this is working content, not a match",
        )
        _make_transcript(fake_home, proj, "abc123.jsonl", "unique-transcript-hit")
        monkeypatch.chdir(proj)

        rc = ccs.main(["unique-transcript-hit", "--messages"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "20260504-foo" in out

    def test_messages_does_not_match_working_file_content(
        self, fake_repos, fake_home, monkeypatch, capsys, force_grep_path
    ):
        """A term in working/ should not appear in --messages results."""
        proj = fake_repos / "myproj"
        _make_session(
            fake_repos, "myproj", "20260504-foo",
            contents="working-only-term",
        )
        # No transcript content with the term
        _make_transcript(fake_home, proj, "abc123.jsonl", "unrelated transcript")
        monkeypatch.chdir(proj)

        rc = ccs.main(["working-only-term", "--messages"])
        assert rc == 1

    def test_messages_flag_value_no_positional(
        self, fake_repos, fake_home, monkeypatch, capsys, force_grep_path
    ):
        """ccs --messages "foo" with no positional should work."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo", contents="irrelevant")
        _make_transcript(fake_home, proj, "t.jsonl", "foo-in-transcript")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--messages", "foo-in-transcript"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "20260504-foo" in out


# ---------------------------------------------------------------------------
# Test 3: --name --contents with same positional query runs both scopes
# ---------------------------------------------------------------------------


class TestMultiScope:
    def test_name_and_contents_combined_both_run(
        self, fake_repos, fake_home, monkeypatch, capsys, force_grep_path
    ):
        proj = fake_repos / "myproj"
        # Session whose name matches "target"
        _make_session(fake_repos, "myproj", "20260504-target-session", contents="no match here")
        # Session whose content matches "target"
        _make_session(fake_repos, "myproj", "20260503-other-name", contents="target in working")
        monkeypatch.chdir(proj)

        rc = ccs.main(["target", "--name", "--contents"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "target-session" in out
        assert "other-name" in out

    def test_multi_scope_tagged_lines(
        self, fake_repos, fake_home, monkeypatch, capsys, force_grep_path
    ):
        """When multiple scopes are active, output lines include scope tags."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-target-name", contents="no hit")
        _make_session(fake_repos, "myproj", "20260503-other", contents="target in content")
        monkeypatch.chdir(proj)

        ccs.main(["target", "--name", "--contents"])
        out = capsys.readouterr().out
        assert "[name]" in out or "[contents]" in out


# ---------------------------------------------------------------------------
# Test 4: Three scopes with different queries
# ---------------------------------------------------------------------------


class TestThreeScopeDifferentQueries:
    def test_three_scopes_each_use_own_query(
        self, fake_repos, fake_home, monkeypatch, capsys, force_grep_path
    ):
        proj = fake_repos / "myproj"
        # name scope: session named with "alpha"
        _make_session(fake_repos, "myproj", "20260504-alpha-session", contents="nope")
        # contents scope: session with "beta" in working file
        _make_session(fake_repos, "myproj", "20260503-other1", contents="beta in working")
        # messages scope: transcript with "gamma"
        _make_session(fake_repos, "myproj", "20260502-other2", contents="nope")
        _make_transcript(fake_home, proj, "t.jsonl", "gamma in transcript")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--name", "alpha", "--contents", "beta", "--messages", "gamma"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "alpha-session" in out
        assert "other1" in out
        assert "other2" in out


# ---------------------------------------------------------------------------
# Test 5: Scope flag with no query and no positional → exit 1
# ---------------------------------------------------------------------------


class TestMissingQuery:
    def test_contents_flag_no_query_no_positional_exits_1(
        self, fake_repos, monkeypatch, capsys
    ):
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(proj)

        rc = ccs.main(["--contents"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "query" in err.lower() or "usage" in err.lower() or "no query" in err.lower()

    def test_messages_flag_no_query_no_positional_exits_1(
        self, fake_repos, monkeypatch, capsys
    ):
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(proj)

        rc = ccs.main(["--messages"])
        assert rc == 1

    def test_name_flag_no_query_no_positional_exits_1(
        self, fake_repos, monkeypatch, capsys
    ):
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(proj)

        rc = ccs.main(["--name"])
        assert rc == 1


# ---------------------------------------------------------------------------
# Test 6: Positional accepted before AND after scope flags
# ---------------------------------------------------------------------------


class TestPositionalOrdering:
    def test_positional_before_flag(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo-session", contents="bar content")
        monkeypatch.chdir(proj)

        rc = ccs.main(["foo", "--name"])
        assert rc == 0
        assert "foo-session" in capsys.readouterr().out

    def test_positional_after_flag(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo-session", contents="bar content")
        monkeypatch.chdir(proj)

        rc = ccs.main(["--name", "--", "foo"])
        assert rc == 0
        assert "foo-session" in capsys.readouterr().out

    def test_bare_positional_unchanged(self, fake_repos, monkeypatch, capsys):
        """Plain ccs <q> must still work (backwards compat)."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo-session")
        monkeypatch.chdir(proj)

        rc = ccs.main(["foo"])
        assert rc == 0
        assert "foo-session" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Test 7: --since accepts ISO date formats
# ---------------------------------------------------------------------------


class TestSinceISOFormats:
    def test_since_iso_date_yyyy_mm_dd(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260101-old-work")
        _make_session(fake_repos, "myproj", "20260504-new-work")
        monkeypatch.chdir(proj)

        rc = ccs.main(["work", "--since", "2026-03-01"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "new-work" in out
        assert "old-work" not in out

    def test_since_iso_datetime_hhmm(self, fake_repos, monkeypatch, capsys):
        """YYYY-MM-DDTHH:MM should work; time portion ignored (date only)."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260101-old-work")
        _make_session(fake_repos, "myproj", "20260504-new-work")
        monkeypatch.chdir(proj)

        rc = ccs.main(["work", "--since", "2026-03-01T08:00"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "new-work" in out
        assert "old-work" not in out

    def test_since_iso_datetime_hhmmss(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260101-old-work")
        _make_session(fake_repos, "myproj", "20260504-new-work")
        monkeypatch.chdir(proj)

        rc = ccs.main(["work", "--since", "2026-03-01T08:00:00"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "new-work" in out
        assert "old-work" not in out

    def test_since_yyyymmdd_still_works(self, fake_repos, monkeypatch, capsys):
        """Legacy YYYYMMDD format must remain accepted."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260101-old-work")
        _make_session(fake_repos, "myproj", "20260504-new-work")
        monkeypatch.chdir(proj)

        rc = ccs.main(["work", "--since", "20260301"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "new-work" in out
        assert "old-work" not in out

    def test_since_all_three_iso_formats_equivalent(self, fake_repos, monkeypatch, capsys):
        """YYYY-MM-DD, YYYY-MM-DDTHH:MM, YYYYMMDD should all filter the same."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260101-old-work")
        _make_session(fake_repos, "myproj", "20260504-new-work")
        monkeypatch.chdir(proj)

        results = []
        for since in ["2026-03-01", "2026-03-01T08:00", "20260301"]:
            ccs.main(["work", "--since", since])
            out = capsys.readouterr().out
            results.append(out)

        assert results[0] == results[1] == results[2]

    def test_since_invalid_rejects_with_error(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(proj)

        rc = ccs.main(["work", "--since", "not-a-date"])
        assert rc == 1
        assert "invalid date" in capsys.readouterr().err.lower()

    def test_since_partial_garbage_rejects(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        proj.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(proj)

        rc = ccs.main(["work", "--since", "2026/03/01"])
        assert rc == 1


# ---------------------------------------------------------------------------
# Test 8: --sort alpha returns ascending alphabetical by basename
# ---------------------------------------------------------------------------


class TestSortAlpha:
    def test_sort_alpha_ascending(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo-new")
        _make_session(fake_repos, "myproj", "20260101-aaa-old")
        _make_session(fake_repos, "myproj", "20260301-mmm-mid")
        monkeypatch.chdir(proj)

        rc = ccs.main(["2026", "--sort", "alpha"])
        assert rc == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines == sorted(lines)

    def test_sort_alpha_is_ascending_not_descending(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-zzz-last")
        _make_session(fake_repos, "myproj", "20260101-aaa-first")
        monkeypatch.chdir(proj)

        rc = ccs.main(["2026", "--sort", "alpha"])
        assert rc == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[0].startswith("20260101")
        assert lines[1].startswith("20260504")


# ---------------------------------------------------------------------------
# Test 9: --sort datetime (or default) returns newest first
# ---------------------------------------------------------------------------


class TestSortDatetime:
    def test_sort_datetime_newest_first(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260101-foo-old")
        _make_session(fake_repos, "myproj", "20260504-foo-new")
        _make_session(fake_repos, "myproj", "20260301-foo-mid")
        monkeypatch.chdir(proj)

        rc = ccs.main(["foo", "--sort", "datetime"])
        assert rc == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines == [
            "20260504-foo-new",
            "20260301-foo-mid",
            "20260101-foo-old",
        ]

    def test_default_sort_is_datetime_newest_first(self, fake_repos, monkeypatch, capsys):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260101-foo-old")
        _make_session(fake_repos, "myproj", "20260504-foo-new")
        monkeypatch.chdir(proj)

        rc = ccs.main(["foo"])
        assert rc == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[0] == "20260504-foo-new"


# ---------------------------------------------------------------------------
# Test 10: Output prefix tags ([name], [contents], [messages]) appear correctly
# ---------------------------------------------------------------------------


class TestOutputPrefixTags:
    def test_name_scope_tag_in_multi_scope_output(
        self, fake_repos, monkeypatch, capsys, force_grep_path
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-target-name", contents="no hit here")
        _make_session(fake_repos, "myproj", "20260503-other", contents="target in content")
        monkeypatch.chdir(proj)

        ccs.main(["target", "--name", "--contents"])
        out = capsys.readouterr().out
        assert "[name]" in out
        assert "[contents]" in out

    def test_single_scope_no_tags(self, fake_repos, monkeypatch, capsys):
        """When only one scope is used, no [scope] tags are needed."""
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo-bar")
        monkeypatch.chdir(proj)

        ccs.main(["foo"])
        out = capsys.readouterr().out
        assert "[name]" not in out
        assert "[contents]" not in out
        assert "[messages]" not in out


# ---------------------------------------------------------------------------
# Test 11: JSON output includes a 'scope' field on each match
# ---------------------------------------------------------------------------


class TestJsonScope:
    def test_json_name_scope_includes_scope_field(
        self, fake_repos, monkeypatch, capsys
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-foo-bar")
        monkeypatch.chdir(proj)

        rc = ccs.main(["foo", "--name", "--json"])
        assert rc == 0
        data = json_mod.loads(capsys.readouterr().out)
        assert len(data) > 0
        for item in data:
            assert "scope" in item

    def test_json_multi_scope_scope_field_values(
        self, fake_repos, fake_home, monkeypatch, capsys, force_grep_path
    ):
        proj = fake_repos / "myproj"
        _make_session(fake_repos, "myproj", "20260504-target-session", contents="no hit")
        _make_session(fake_repos, "myproj", "20260503-other", contents="target in content")
        monkeypatch.chdir(proj)

        rc = ccs.main(["target", "--name", "--contents", "--json"])
        assert rc == 0
        data = json_mod.loads(capsys.readouterr().out)
        scopes = {item["scope"] for item in data}
        assert "name" in scopes
        assert "contents" in scopes
