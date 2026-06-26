"""Tests for cccs_hooks.session_tag — SessionStart hook that writes <session_id>.tag files."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cccs_hooks import session_tag


# ---------------------------------------------------------------------------
# encode_path
# ---------------------------------------------------------------------------

def test_encode_path_replaces_slashes_with_dashes():
    assert session_tag.encode_path("/home/alice") == "-home-alice"


def test_encode_path_replaces_dots_with_dashes():
    # /home/alice/.claude -> -home-alice--claude (each non-alnum char → -)
    assert session_tag.encode_path("/home/alice/.claude") == "-home-alice--claude"


def test_encode_path_known_mnt_path():
    encoded = session_tag.encode_path("/mnt/c/Users/alice/repos/myproject")
    assert encoded == "-mnt-c-Users-alice-repos-myproject"


def test_encode_path_preserves_alphanumeric():
    assert session_tag.encode_path("/repos/myProject123") == "-repos-myProject123"


# ---------------------------------------------------------------------------
# main() — no CLD_SESSION_TAG
# ---------------------------------------------------------------------------

def test_no_tag_env_returns_zero_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("CLD_SESSION_TAG", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin("{}"))
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    assert list(tmp_path.rglob("*.tag")) == []


# ---------------------------------------------------------------------------
# main() — happy path: session_id present in stdin JSON
# ---------------------------------------------------------------------------

def test_writes_tag_file_to_tags_dir(tmp_path, monkeypatch):
    cwd = "/mnt/c/Users/alice/OneDrive/claude/oneshot"
    payload = json.dumps({"session_id": "abc-123", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    # Flat layout: <tags_dir>/<uuid>.tag (no cwd encoding)
    tag_file = tmp_path / "abc-123.tag"
    assert tag_file.exists()
    assert tag_file.read_text() == "my-feature\n"


def test_creates_tags_dir_if_absent(tmp_path, monkeypatch):
    cwd = "/some/new/project"
    payload = json.dumps({"session_id": "uuid-xyz", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "cool-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    new_tags_dir = tmp_path / "new-tags-dir"
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(new_tags_dir))

    rc = session_tag.main()

    assert rc == 0
    assert new_tags_dir.is_dir()
    assert (new_tags_dir / "uuid-xyz.tag").read_text() == "cool-tag\n"


def test_tag_file_contains_tag_with_trailing_newline(tmp_path, monkeypatch):
    cwd = "/my/project"
    payload = json.dumps({"session_id": "sid-1", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "oneshot-my-task")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))

    session_tag.main()

    tag_file = tmp_path / "sid-1.tag"
    content = tag_file.read_text()
    assert content == "oneshot-my-task\n"


# ---------------------------------------------------------------------------
# main() — cwd fallback via CLAUDE_PROJECT_DIR env var
# ---------------------------------------------------------------------------

def test_falls_back_to_claude_project_dir_env_when_no_cwd_in_stdin(tmp_path, monkeypatch):
    """When stdin JSON has no 'cwd', use CLAUDE_PROJECT_DIR env var.

    With the flat layout, the tag file location is keyed by UUID only —
    the cwd is not used to build the file path. This test still verifies the
    hook writes the file successfully when cwd comes from the env fallback.
    """
    cwd = "/env/project/path"
    payload = json.dumps({"session_id": "env-fallback-uuid"})
    monkeypatch.setenv("CLD_SESSION_TAG", "fallback-tag")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", cwd)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    # Flat layout: just uuid.tag
    tag_file = tmp_path / "env-fallback-uuid.tag"
    assert tag_file.exists()


def test_claude_project_dir_not_used_when_cwd_in_stdin(tmp_path, monkeypatch):
    """stdin cwd and env cwd both result in the same flat file (uuid-keyed)."""
    stdin_cwd = "/stdin/cwd"
    env_cwd = "/env/cwd"
    payload = json.dumps({"session_id": "prio-test", "cwd": stdin_cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "prio-tag")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", env_cwd)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))

    session_tag.main()

    # Flat layout: tag file is always at <tags_dir>/<uuid>.tag
    assert (tmp_path / "prio-test.tag").exists()


# ---------------------------------------------------------------------------
# main() — missing session_id
# ---------------------------------------------------------------------------

def test_missing_session_id_returns_zero_and_logs(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"cwd": "/some/path"})
    monkeypatch.setenv("CLD_SESSION_TAG", "some-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    assert list(tmp_path.rglob("*.tag")) == []
    err = capsys.readouterr().err
    assert "[session-tag]" in err


# ---------------------------------------------------------------------------
# main() — bad stdin JSON
# ---------------------------------------------------------------------------

def test_invalid_json_on_stdin_returns_zero_and_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLD_SESSION_TAG", "some-tag")
    monkeypatch.setattr("sys.stdin", _stdin("NOT JSON"))
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "[session-tag]" in err


# ---------------------------------------------------------------------------
# main() — write failure is silent (never raises)
# ---------------------------------------------------------------------------

def test_write_failure_returns_zero_and_logs(tmp_path, monkeypatch, capsys):
    cwd = "/write/fail/path"
    payload = json.dumps({"session_id": "bad-write", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "fail-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))

    # Point CCCS_SESSION_TAGS_DIR to a FILE (not a dir) so mkdir fails with OSError
    blocker = tmp_path / "blocker"
    blocker.write_text("block")
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(blocker))

    rc = session_tag.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "[session-tag]" in err


# ---------------------------------------------------------------------------
# main() — .last-opened sentinel file
# ---------------------------------------------------------------------------

def test_last_opened_created_when_cld_session_dir_set(tmp_path, monkeypatch):
    """CLD_SESSION_DIR set and dir exists: .last-opened is created."""
    sess_dir = tmp_path / "sess"
    sess_dir.mkdir()
    cwd = "/some/project"
    payload = json.dumps({"session_id": "open-test", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "open-tag")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tags_dir))

    rc = session_tag.main()

    assert rc == 0
    assert (sess_dir / ".last-opened").exists()


def test_last_opened_mtime_updated_when_already_exists(tmp_path, monkeypatch):
    """CLD_SESSION_DIR set, .last-opened already exists: mtime is updated."""
    import os, time
    sess_dir = tmp_path / "sess"
    sess_dir.mkdir()
    sentinel = sess_dir / ".last-opened"
    sentinel.touch()
    old_time = time.time() - 3600
    os.utime(sentinel, (old_time, old_time))
    old_mtime = sentinel.stat().st_mtime

    cwd = "/some/project"
    payload = json.dumps({"session_id": "open-test2", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "open-tag2")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tags_dir))

    session_tag.main()

    new_mtime = sentinel.stat().st_mtime
    assert new_mtime > old_mtime


def test_last_opened_not_written_when_no_cld_session_dir(tmp_path, monkeypatch):
    """CLD_SESSION_DIR not set: no .last-opened written, no exception raised."""
    cwd = "/some/project"
    payload = json.dumps({"session_id": "no-dir", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "no-dir-tag")
    monkeypatch.delenv("CLD_SESSION_DIR", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tags_dir))

    rc = session_tag.main()

    assert rc == 0
    # No .last-opened anywhere in tmp_path
    sentinels = list(tmp_path.rglob(".last-opened"))
    assert sentinels == []


def test_last_opened_oserror_logs_to_stderr_no_exception(tmp_path, monkeypatch, capsys):
    """.touch() raises OSError: error printed to stderr (contains [session-tag]), no exception propagated."""
    # Point CLD_SESSION_DIR at a non-existent nested path so .touch() raises
    missing_dir = tmp_path / "does" / "not" / "exist"
    cwd = "/some/project"
    payload = json.dumps({"session_id": "oserr-test", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "oserr-tag")
    monkeypatch.setenv("CLD_SESSION_DIR", str(missing_dir))
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tags_dir))

    rc = session_tag.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "[session-tag]" in err


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _stdin:
    """Minimal stdin mock that provides .read()."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text
