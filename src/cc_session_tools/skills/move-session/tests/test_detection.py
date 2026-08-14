"""Tests for detect_active_source_session - the four cases that trigger
or don't trigger the in-session refusal."""
from __future__ import annotations

import os
import time

import pytest


def _backdate(p, seconds):
    """Set mtime/atime of p to `seconds` ago."""
    t = time.time() - seconds
    os.utime(p, (t, t))


class TestDetectActive:
    def test_not_in_cc_never_triggers(self, ms, tmp_home, projects_root,
                                      make_session, monkeypatch):
        """Outside CC, no env vars set: refusal never fires regardless of
        jsonl mtime or cwd."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        session_dir, jsonl, _ = make_session("proj", "20260503-test")
        src_cwd = str(session_dir.parent.parent)
        key_dir = jsonl.parent
        # Even with a fresh write and cwd-match, in_cc gates the whole thing.
        monkeypatch.chdir(src_cwd)
        is_active, reasons = ms.detect_active_source_session(src_cwd, jsonl, key_dir)
        assert is_active is False

    def test_in_cc_with_recent_write_triggers(self, ms, tmp_home, make_session,
                                              monkeypatch):
        """Recent jsonl write (<30s ago) inside CC: refuse."""
        monkeypatch.setenv("CLAUDECODE", "1")
        session_dir, jsonl, _ = make_session("proj", "20260503-test")
        src_cwd = str(session_dir.parent.parent)
        # jsonl was just written by make_session, so mtime is fresh
        is_active, reasons = ms.detect_active_source_session(
            src_cwd, jsonl, jsonl.parent)
        assert is_active is True
        assert any("actively appending" in r for r in reasons)

    def test_in_cc_old_jsonl_no_cwd_match_does_not_trigger(
            self, ms, tmp_home, make_session, monkeypatch, tmp_path):
        """Old jsonl, cwd is somewhere unrelated: don't trigger."""
        monkeypatch.setenv("CLAUDECODE", "1")
        session_dir, jsonl, _ = make_session("proj", "20260503-test")
        _backdate(jsonl, 600)
        src_cwd = str(session_dir.parent.parent)
        # cwd is a completely unrelated dir
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        is_active, _ = ms.detect_active_source_session(
            src_cwd, jsonl, jsonl.parent)
        assert is_active is False

    def test_in_cc_old_jsonl_cwd_match_freshest_triggers(
            self, ms, tmp_home, make_session, monkeypatch):
        """Idle session: cwd matches src_cwd AND src jsonl is the only/freshest
        in the project key dir. This is the new (b) trigger added in 3.3."""
        monkeypatch.setenv("CLAUDECODE", "1")
        session_dir, jsonl, _ = make_session("proj", "20260503-test")
        _backdate(jsonl, 600)
        src_cwd = str(session_dir.parent.parent)
        monkeypatch.chdir(src_cwd)
        is_active, reasons = ms.detect_active_source_session(
            src_cwd, jsonl, jsonl.parent)
        assert is_active is True
        assert any("most-recently-modified" in r for r in reasons)

    def test_in_cc_sibling_session_does_not_trigger(
            self, ms, tmp_home, make_session, monkeypatch):
        """In CC, cwd matches src_cwd, but a SIBLING session's jsonl is fresher
        (i.e. *that* sibling is the one currently running). Moving the older
        sibling should NOT be refused."""
        monkeypatch.setenv("CLAUDECODE", "1")
        # Build the source we want to move (older).
        old_session, old_jsonl, _ = make_session("proj", "20260503-old")
        _backdate(old_jsonl, 600)
        # Build the "currently-running" sibling, freshest in the key dir.
        new_session, new_jsonl, _ = make_session("proj", "20260503-running")
        # new_jsonl is fresh by default (created seconds ago), so it's freshest.
        src_cwd = str(old_session.parent.parent)
        monkeypatch.chdir(src_cwd)
        is_active, _ = ms.detect_active_source_session(
            src_cwd, old_jsonl, old_jsonl.parent)
        assert is_active is False
