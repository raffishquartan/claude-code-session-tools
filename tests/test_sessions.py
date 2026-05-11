from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import sessions


def test_is_session_basename_accepts_yyyymmdd_dash_tag():
    assert sessions.is_session_basename("20260504-oneshot-foo") is True


def test_is_session_basename_accepts_to_form():
    assert sessions.is_session_basename("20260504-to-20260509-oneshot-foo") is True


def test_is_session_basename_rejects_no_date_prefix():
    assert sessions.is_session_basename("oneshot-foo") is False


def test_is_session_basename_rejects_short_date():
    assert sessions.is_session_basename("2026504-foo") is False


def test_session_start_date_returns_first_date():
    assert sessions.session_start_date("20260504-to-20260509-foo") == "20260504"


def test_session_start_date_returns_none_for_invalid():
    assert sessions.session_start_date("not-a-session") is None


def test_session_tag_extracts_tag_from_simple_form():
    assert sessions.session_tag("20260504-oneshot-foo") == "oneshot-foo"


def test_session_tag_extracts_tag_from_to_form():
    assert sessions.session_tag("20260504-to-20260509-oneshot-foo") == "oneshot-foo"


def test_session_tag_returns_none_for_invalid():
    assert sessions.session_tag("not-a-session") is None


def test_iter_sessions_yields_only_session_dirs(tmp_path):
    cc = tmp_path / "cc-sessions"
    (cc / "20260504-foo").mkdir(parents=True)
    (cc / "20260503-bar").mkdir()
    (cc / "not-a-session").mkdir()
    (cc / "20260502-baz" / "working").mkdir(parents=True)

    names = sorted(s.name for s in sessions.iter_sessions(cc))
    assert names == ["20260502-baz", "20260503-bar", "20260504-foo"]


def test_find_matching_sessions_substring_match(tmp_path):
    root = tmp_path / "myroot"
    proj = root / "myproject"
    cc = proj / "cc-sessions"
    (cc / "20260504-foo-bar").mkdir(parents=True)
    (cc / "20260503-baz").mkdir()

    matches = sessions.find_matching_sessions("foo", roots=[root])
    assert len(matches) == 1
    assert matches[0].basename == "20260504-foo-bar"
    assert matches[0].project_dir == proj


def test_find_matching_sessions_returns_empty_for_no_match(tmp_path):
    root = tmp_path / "myroot"
    proj = root / "myproject"
    cc = proj / "cc-sessions"
    (cc / "20260504-foo").mkdir(parents=True)

    assert sessions.find_matching_sessions("nope", roots=[root]) == []


def test_grep_session_returns_match_with_one_line_context(tmp_path):
    sess = tmp_path / "20260504-foo"
    (sess / "working").mkdir(parents=True)
    (sess / "working" / "WORKLOG.md").write_text(
        "before line\n"
        "matching FLAMBE here\n"
        "after line\n"
        "unrelated\n"
    )
    out = sessions.grep_session(sess, "FLAMBE")
    # Should contain the match line plus context, but no file:lineno noise.
    text = "\n".join(out)
    assert "before line" in text
    assert "matching FLAMBE here" in text
    assert "after line" in text


def test_grep_session_returns_empty_when_no_match(tmp_path):
    sess = tmp_path / "20260504-foo"
    (sess / "working").mkdir(parents=True)
    (sess / "working" / "WORKLOG.md").write_text("nothing relevant\n")
    assert sessions.grep_session(sess, "FLAMBE") == []


def test_grep_session_skips_binary_files(tmp_path):
    sess = tmp_path / "20260504-foo"
    sess.mkdir()
    (sess / "binary.bin").write_bytes(b"\x00\x01\x02FLAMBE\x03\x04")
    (sess / "text.md").write_text("FLAMBE in text\n")
    out = sessions.grep_session(sess, "FLAMBE")
    text = "\n".join(out)
    assert "FLAMBE in text" in text
    assert "\x00" not in text


class TestEnumerateSessionFiles:
    def test_returns_files_and_total_bytes(self, tmp_path):
        sess = tmp_path / "20260504-foo"
        (sess / "working").mkdir(parents=True)
        (sess / "working" / "a.md").write_text("hello\n")  # 6 bytes
        (sess / "working" / "b.md").write_text("hi\n")  # 3 bytes

        files, total_bytes, skipped = sessions.enumerate_session_files(sess)
        assert {p.name for p in files} == {"a.md", "b.md"}
        assert total_bytes == 9
        assert skipped == 0

    def test_max_bytes_skips_oversized_files(self, tmp_path):
        sess = tmp_path / "20260504-foo"
        sess.mkdir()
        (sess / "small.md").write_text("hi")  # 2 bytes
        (sess / "big.bin").write_bytes(b"x" * 1000)  # 1000 bytes

        files, total_bytes, skipped = sessions.enumerate_session_files(sess, max_bytes=100)
        assert {p.name for p in files} == {"small.md"}
        assert total_bytes == 2
        assert skipped == 1

    def test_returns_empty_for_empty_session(self, tmp_path):
        sess = tmp_path / "20260504-foo"
        sess.mkdir()
        files, total_bytes, skipped = sessions.enumerate_session_files(sess)
        assert files == []
        assert total_bytes == 0
        assert skipped == 0


class TestGrepFiles:
    def test_grep_files_finds_matches_in_provided_files(self, tmp_path):
        a = tmp_path / "a.md"
        a.write_text("alpha\nFLAMBE here\nomega\n")
        b = tmp_path / "b.md"
        b.write_text("nothing\n")
        out = sessions.grep_files([a, b], "FLAMBE", context=1, cwd=tmp_path)
        text = "\n".join(out)
        assert "FLAMBE here" in text

    def test_grep_files_returns_empty_when_no_files(self, tmp_path):
        assert sessions.grep_files([], "FLAMBE", context=1, cwd=tmp_path) == []

    def test_grep_files_returns_empty_on_no_match(self, tmp_path):
        a = tmp_path / "a.md"
        a.write_text("nothing here\n")
        assert sessions.grep_files([a], "FLAMBE", context=1, cwd=tmp_path) == []


def test_transcript_dir_encoding_simple():
    # slashes become dashes, producing the encoded project dir name
    result = sessions.transcript_dir_for_project(Path("/example/repos/my-project"))
    assert result == Path.home() / ".claude" / "projects" / "-example-repos-my-project"


def test_transcript_dir_encoding_with_dots():
    # dots are also replaced with dashes
    result = sessions.transcript_dir_for_project(Path("/example/.local/share"))
    assert result == Path.home() / ".claude" / "projects" / "-example--local-share"


def test_transcript_dir_returns_path_object():
    result = sessions.transcript_dir_for_project(Path("/example/foo"))
    assert isinstance(result, Path)
