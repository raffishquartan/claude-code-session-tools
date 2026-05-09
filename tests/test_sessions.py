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
