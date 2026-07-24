"""Tests for cccs_hooks.after_response — Stop hook that records last_active
into sessions.db."""
from __future__ import annotations

from pathlib import Path

from cccs_hooks import after_response
from cc_session_tools.lib import sessions_db


def test_last_active_recorded_when_cld_session_dir_set(tmp_path: Path, monkeypatch) -> None:
    """CLD_SESSION_DIR shaped like <project>/cc-sessions/<basename>: a row is
    upserted with a fresh last_active timestamp."""
    project = tmp_path / "myproj"
    sess_dir = project / "cc-sessions" / "20260711-my-feature"
    sess_dir.mkdir(parents=True)
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    db_dir = tmp_path / "db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    rc = after_response.main()

    assert rc == 0
    rows = sessions_db.list_sessions(path=db_dir / "sessions.db")
    assert len(rows) == 1
    assert rows[0].basename == "20260711-my-feature"
    assert rows[0].last_active > 0.0


def test_last_active_updates_existing_row_repeatedly(tmp_path: Path, monkeypatch) -> None:
    """Fires after every response — each call must bump the timestamp forward."""
    project = tmp_path / "myproj"
    sess_dir = project / "cc-sessions" / "20260711-my-feature"
    sess_dir.mkdir(parents=True)
    db_dir = tmp_path / "db"
    db_path = db_dir / "sessions.db"
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))
    sessions_db.touch_last_active(project, "20260711-my-feature", path=db_path, when=100.0)

    after_response.main()

    rows = sessions_db.list_sessions(path=db_path)
    assert rows[0].last_active > 100.0


def test_last_active_not_recorded_when_cld_session_dir_not_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CLD_SESSION_DIR", raising=False)
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))

    rc = after_response.main()

    assert rc == 0
    assert not (tmp_path / "sessions.db").exists()


def test_last_active_not_recorded_when_dir_not_shaped_like_cc_sessions(
    tmp_path: Path, monkeypatch
) -> None:
    sess_dir = tmp_path / "sess"
    sess_dir.mkdir()
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))
    db_dir = tmp_path / "db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    rc = after_response.main()

    assert rc == 0
    assert sessions_db.list_sessions(path=db_dir / "sessions.db") == []


def test_write_failure_logs_to_stderr_no_exception(tmp_path: Path, monkeypatch, capsys) -> None:
    """DB write failure (e.g. unwritable target): error printed to stderr
    (contains [after-response]), no exception propagated."""
    project = tmp_path / "myproj"
    sess_dir = project / "cc-sessions" / "20260711-my-feature"
    sess_dir.mkdir(parents=True)
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))

    # Point CCST_SESSIONS_DIR at a FILE (not a dir) so mkdir fails with OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("block")
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(blocker))

    rc = after_response.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "[after-response]" in err
