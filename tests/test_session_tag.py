"""Tests for cccs_hooks.session_tag — SessionStart hook that records tags and
.last-opened activity into sessions.db."""
from __future__ import annotations

import json

import pytest

from cccs_hooks import session_tag
from cc_session_tools.lib import sessions_db


@pytest.fixture(autouse=True)
def _clear_cld_env(monkeypatch):
    """Hermeticity: this suite may run inside a real ccd/ccr session whose
    CLD_SESSION_* env vars would otherwise leak into tests that assume them
    unset. In CI these are already unset, so this is a no-op there."""
    for var in ("CLD_SESSION_TAG", "CLD_SESSION_DIR", "CLD_SESSION_MODE"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# encode_path
# ---------------------------------------------------------------------------

def test_encode_path_replaces_slashes_with_dashes():
    assert session_tag.encode_path("/home/alice") == "-home-alice"


def test_encode_path_replaces_dots_with_dashes():
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
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    assert not (tmp_path / "sessions.db").exists()


# ---------------------------------------------------------------------------
# main() — happy path: session_id present in stdin JSON
# ---------------------------------------------------------------------------

def test_records_tag_in_sessions_db(tmp_path, monkeypatch):
    payload = json.dumps({"session_id": "abc-123", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    db_path = tmp_path / "sessions.db"
    assert sessions_db.lookup_tags(["abc-123"], path=db_path) == {"abc-123": "my-feature"}


def test_creates_sessions_db_if_absent(tmp_path, monkeypatch):
    payload = json.dumps({"session_id": "uuid-xyz", "cwd": "/some/new/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "cool-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    new_dir = tmp_path / "new-sessions-dir"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(new_dir))

    rc = session_tag.main()

    assert rc == 0
    assert (new_dir / "sessions.db").exists()


# ---------------------------------------------------------------------------
# main() — missing session_id
# ---------------------------------------------------------------------------

def test_missing_session_id_returns_zero_and_logs(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"cwd": "/some/path"})
    monkeypatch.setenv("CLD_SESSION_TAG", "some-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    assert not (tmp_path / "sessions.db").exists()
    err = capsys.readouterr().err
    assert "[session-tag]" in err


# ---------------------------------------------------------------------------
# main() — bad stdin JSON
# ---------------------------------------------------------------------------

def test_invalid_json_on_stdin_returns_zero_and_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLD_SESSION_TAG", "some-tag")
    monkeypatch.setattr("sys.stdin", _stdin("NOT JSON"))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "[session-tag]" in err


# ---------------------------------------------------------------------------
# main() — write failure is silent (never raises)
# ---------------------------------------------------------------------------

def test_write_failure_returns_zero_and_logs(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"session_id": "bad-write", "cwd": "/write/fail/path"})
    monkeypatch.setenv("CLD_SESSION_TAG", "fail-tag")
    monkeypatch.setattr("sys.stdin", _stdin(payload))

    # Point CCST_SESSIONS_DIR at a FILE (not a dir) so mkdir fails with OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("block")
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(blocker))

    rc = session_tag.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "[session-tag]" in err


# ---------------------------------------------------------------------------
# main() — .last-opened -> sessions.db row
# ---------------------------------------------------------------------------

def test_last_opened_recorded_when_cld_session_dir_set(tmp_path, monkeypatch):
    """CLD_SESSION_DIR shaped like <project>/cc-sessions/<basename>: a row is
    upserted with a fresh last_opened timestamp."""
    project = tmp_path / "myproj"
    sess_dir = project / "cc-sessions" / "20260711-my-feature"
    sess_dir.mkdir(parents=True)
    payload = json.dumps({"session_id": "open-test", "cwd": str(project)})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    db_dir = tmp_path / "db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    rc = session_tag.main()

    assert rc == 0
    rows = sessions_db.list_sessions(path=db_dir / "sessions.db")
    assert len(rows) == 1
    assert rows[0].basename == "20260711-my-feature"
    assert rows[0].project_dir == project
    assert rows[0].last_opened > 0.0


def test_last_opened_mtime_updated_when_row_already_exists(tmp_path, monkeypatch):
    project = tmp_path / "myproj"
    sess_dir = project / "cc-sessions" / "20260711-my-feature"
    sess_dir.mkdir(parents=True)
    db_path = tmp_path / "db" / "sessions.db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_path.parent))
    sessions_db.touch_last_opened(project, "20260711-my-feature", path=db_path, when=100.0)

    payload = json.dumps({"session_id": "open-test2", "cwd": str(project)})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setattr("sys.stdin", _stdin(payload))

    session_tag.main()

    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].last_opened > 100.0


def test_last_opened_not_recorded_when_no_cld_session_dir(tmp_path, monkeypatch):
    payload = json.dumps({"session_id": "no-dir", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "no-dir-tag")
    monkeypatch.delenv("CLD_SESSION_DIR", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = session_tag.main()

    assert rc == 0
    assert sessions_db.list_sessions(path=tmp_path / "sessions.db") == []


def test_last_opened_not_recorded_when_dir_not_shaped_like_cc_sessions(tmp_path, monkeypatch):
    """CLD_SESSION_DIR not under a cc-sessions/ parent: no row written, no error."""
    sess_dir = tmp_path / "sess"
    sess_dir.mkdir()
    payload = json.dumps({"session_id": "shape-test", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "shape-tag")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    db_dir = tmp_path / "db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    rc = session_tag.main()

    assert rc == 0
    assert sessions_db.list_sessions(path=db_dir / "sessions.db") == []


# ---------------------------------------------------------------------------
# main() — additionalContext emission (unaffected by the storage rewrite)
# ---------------------------------------------------------------------------

def test_no_tag_emits_no_additional_context(monkeypatch, capsys):
    monkeypatch.delenv("CLD_SESSION_TAG", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin("{}"))

    session_tag.main()

    assert capsys.readouterr().out == ""


def test_new_mode_additional_context(tmp_path, monkeypatch, capsys):
    sess_dir = tmp_path / "cc-sessions" / "20260711-my-feature"
    payload = json.dumps({"session_id": "sid-new", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setenv("CLD_SESSION_MODE", "new")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))

    rc = session_tag.main()
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "ccd shell wrapper" in msg
    assert "my-feature" in msg
    assert str(sess_dir) in msg
    assert "/rename is unnecessary" in msg


def test_resume_mode_additional_context(tmp_path, monkeypatch, capsys):
    sess_dir = tmp_path / "cc-sessions" / "20260701-my-feature"
    payload = json.dumps({"session_id": "sid-resume", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setenv("CLD_SESSION_MODE", "resume")
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))

    rc = session_tag.main()
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "ccr shell wrapper" in msg
    assert "being resumed today" in msg
    assert str(sess_dir) in msg


def test_defaults_to_new_mode_when_cld_session_mode_unset(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"session_id": "sid-default", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(tmp_path / "sess"))
    monkeypatch.delenv("CLD_SESSION_MODE", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))

    session_tag.main()
    out = json.loads(capsys.readouterr().out)

    assert "ccd shell wrapper" in out["hookSpecificOutput"]["additionalContext"]


def test_additional_context_emitted_even_when_session_id_missing(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.setenv("CLD_SESSION_DIR", str(tmp_path / "sess"))
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))

    rc = session_tag.main()
    err = capsys.readouterr()

    assert rc == 0
    assert "[session-tag]" in err.err
    out = json.loads(err.out)
    assert "my-feature" in out["hookSpecificOutput"]["additionalContext"]


def test_session_dir_falls_back_to_date_tag_when_cld_session_dir_unset(tmp_path, monkeypatch, capsys):
    payload = json.dumps({"session_id": "sid-fallback", "cwd": "/some/project"})
    monkeypatch.setenv("CLD_SESSION_TAG", "my-feature")
    monkeypatch.delenv("CLD_SESSION_DIR", raising=False)
    monkeypatch.setattr("sys.stdin", _stdin(payload))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path / "db"))

    session_tag.main()
    out = json.loads(capsys.readouterr().out)

    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "cc-sessions/" in msg
    assert "-my-feature/" in msg


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _stdin:
    """Minimal stdin mock that provides .read()."""

    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text
