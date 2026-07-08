"""Tests for cccs_hooks.after_response."""
from __future__ import annotations

from pathlib import Path

from cccs_hooks import after_response


def test_last_active_created_when_cld_session_dir_set(tmp_path: Path, monkeypatch) -> None:
    """CLD_SESSION_DIR set, dir exists: .last-active is created after main() runs."""
    sess_dir = tmp_path / "sess"
    sess_dir.mkdir()
    monkeypatch.setenv("CLD_SESSION_DIR", str(sess_dir))

    rc = after_response.main()

    assert rc == 0
    assert (sess_dir / ".last-active").exists()


def test_last_active_not_written_when_cld_session_dir_not_set(tmp_path: Path, monkeypatch) -> None:
    """CLD_SESSION_DIR not set: no .last-active written, no exception raised."""
    monkeypatch.delenv("CLD_SESSION_DIR", raising=False)

    rc = after_response.main()

    assert rc == 0
    sentinels = list(tmp_path.rglob(".last-active"))
    assert sentinels == []


def test_last_active_oserror_logs_to_stderr_no_exception(tmp_path: Path, monkeypatch, capsys) -> None:
    """.touch() raises OSError: error printed to stderr, no exception propagated."""
    missing_dir = tmp_path / "does" / "not" / "exist"
    monkeypatch.setenv("CLD_SESSION_DIR", str(missing_dir))

    rc = after_response.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "[after-response]" in err
