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
    assert session_tag.encode_path("/home/alice") == "-home-chris"


def test_encode_path_replaces_dots_with_dashes():
    # /home/alice/.claude -> -home-chris--claude (each non-alnum char → -)
    assert session_tag.encode_path("/home/alice/.claude") == "-home-chris--claude"


def test_encode_path_known_oneshot_path():
    encoded = session_tag.encode_path("/mnt/c/Users/alice/OneDrive/claude/oneshot")
    assert encoded == "-mnt-c-Users-cfoge-OneDrive-claude-oneshot"


def test_encode_path_preserves_alphanumeric():
    assert session_tag.encode_path("/repos/myProject123") == "-repos-myProject123"


# ---------------------------------------------------------------------------
# main() — no CLD_SESSION_TAG
# ---------------------------------------------------------------------------

def test_no_tag_env_returns_zero_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("CLD_SESSION_TAG", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin("{}"))
    monkeypatch.setattr(session_tag, "DEFAULT_PROJECTS_DIR", tmp_path)

    rc = session_tag.main()

    assert rc == 0
    assert list(tmp_path.rglob("*.tag")) == []


# ---------------------------------------------------------------------------
# main() — happy path: session_id present in stdin JSON
# ---------------------------------------------------------------------------

def test_writes_tag_file_to_projects_dir(tmp_path, monkeypatch):
    cwd = "/mnt/c/Users/alice/OneDrive/claude/oneshot"
    payload = json.dumps({"session_id": "abc-123", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setattr(session_tag, "DEFAULT_PROJECTS_DIR", tmp_path)

    rc = session_tag.main()

    assert rc == 0
    tag_file = tmp_path / session_tag.encode_path(cwd) / "abc-123.tag"
    assert tag_file.exists()
    assert tag_file.read_text() == "my-feature\n"


def test_creates_project_dir_if_absent(tmp_path, monkeypatch):
    cwd = "/some/new/project"
    payload = json.dumps({"session_id": "uuid-xyz", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "cool-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setattr(session_tag, "DEFAULT_PROJECTS_DIR", tmp_path)

    rc = session_tag.main()

    assert rc == 0
    encoded_dir = tmp_path / session_tag.encode_path(cwd)
    assert encoded_dir.is_dir()
    assert (encoded_dir / "uuid-xyz.tag").read_text() == "cool-tag\n"


def test_tag_file_contains_tag_with_trailing_newline(tmp_path, monkeypatch):
    cwd = "/my/project"
    payload = json.dumps({"session_id": "sid-1", "cwd": cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "oneshot-my-task")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setattr(session_tag, "DEFAULT_PROJECTS_DIR", tmp_path)

    session_tag.main()

    tag_file = tmp_path / session_tag.encode_path(cwd) / "sid-1.tag"
    content = tag_file.read_text()
    assert content == "oneshot-my-task\n"


# ---------------------------------------------------------------------------
# main() — cwd fallback via CLAUDE_PROJECT_DIR env var
# ---------------------------------------------------------------------------

def test_falls_back_to_claude_project_dir_env_when_no_cwd_in_stdin(tmp_path, monkeypatch):
    """When stdin JSON has no 'cwd', use CLAUDE_PROJECT_DIR env var."""
    cwd = "/env/project/path"
    payload = json.dumps({"session_id": "env-fallback-uuid"})
    monkeypatch.setenv("CLD_SESSION_TAG", "fallback-tag")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", cwd)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setattr(session_tag, "DEFAULT_PROJECTS_DIR", tmp_path)

    rc = session_tag.main()

    assert rc == 0
    tag_file = tmp_path / session_tag.encode_path(cwd) / "env-fallback-uuid.tag"
    assert tag_file.exists()


def test_claude_project_dir_not_used_when_cwd_in_stdin(tmp_path, monkeypatch):
    """stdin cwd takes priority over CLAUDE_PROJECT_DIR."""
    stdin_cwd = "/stdin/cwd"
    env_cwd = "/env/cwd"
    payload = json.dumps({"session_id": "prio-test", "cwd": stdin_cwd})
    monkeypatch.setenv("CLD_SESSION_TAG", "prio-tag")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", env_cwd)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setattr(session_tag, "DEFAULT_PROJECTS_DIR", tmp_path)

    session_tag.main()

    # Tag file should be in the stdin_cwd directory, not env_cwd
    assert (tmp_path / session_tag.encode_path(stdin_cwd) / "prio-test.tag").exists()
    assert not (tmp_path / session_tag.encode_path(env_cwd) / "prio-test.tag").exists()


# ---------------------------------------------------------------------------
# main() — missing session_id
# ---------------------------------------------------------------------------

def test_missing_session_id_returns_zero_and_logs(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"cwd": "/some/path"})
    monkeypatch.setenv("CLD_SESSION_TAG", "some-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setattr(session_tag, "DEFAULT_PROJECTS_DIR", tmp_path)

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
    monkeypatch.setattr(session_tag, "DEFAULT_PROJECTS_DIR", tmp_path)

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

    # Point to a FILE (not a dir) so mkdir fails with OSError
    blocker = tmp_path / "blocker"
    blocker.write_text("block")
    monkeypatch.setattr(session_tag, "DEFAULT_PROJECTS_DIR", blocker)

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
