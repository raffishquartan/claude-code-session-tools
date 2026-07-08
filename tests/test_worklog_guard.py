"""Tests for cccs_hooks.worklog_guard."""
from __future__ import annotations

import io
from pathlib import Path

from cccs_hooks import worklog_guard


# ---------- check_worklog_stale ----------

def test_no_worklog_returns_none(tmp_path: Path) -> None:
    assert worklog_guard.check_worklog_stale(tmp_path) is None


def test_recently_modified_worklog_returns_none(tmp_path: Path) -> None:
    working = tmp_path / "working"
    working.mkdir()
    (working / "WORKLOG.md").write_text("# Worklog")
    assert worklog_guard.check_worklog_stale(tmp_path, stale_seconds=3600) is None


def test_stale_worklog_returns_reason(tmp_path: Path) -> None:
    import os
    import time

    working = tmp_path / "working"
    working.mkdir()
    worklog = working / "WORKLOG.md"
    worklog.write_text("# Worklog")
    old_time = time.time() - 7200
    os.utime(worklog, (old_time, old_time))

    reason = worklog_guard.check_worklog_stale(tmp_path, stale_seconds=3600)

    assert reason is not None
    assert "WORKLOG.md" in reason
    assert "CCCS_ALLOW_STALE_WORKLOG" in reason


# ---------- main() ----------

def test_main_allows_when_cld_session_dir_not_set(monkeypatch) -> None:
    monkeypatch.delenv("CLD_SESSION_DIR", raising=False)
    monkeypatch.delenv("CCCS_ALLOW_STALE_WORKLOG", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    assert worklog_guard.main() == 0


def test_main_allows_when_no_worklog_yet(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLD_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("CCCS_ALLOW_STALE_WORKLOG", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    assert worklog_guard.main() == 0


def test_main_blocks_on_stale_worklog(tmp_path: Path, monkeypatch, capsys) -> None:
    import os
    import time

    working = tmp_path / "working"
    working.mkdir()
    worklog = working / "WORKLOG.md"
    worklog.write_text("# Worklog")
    old_time = time.time() - 7200
    os.utime(worklog, (old_time, old_time))

    monkeypatch.setenv("CLD_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("CCCS_ALLOW_STALE_WORKLOG", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    rc = worklog_guard.main()

    assert rc == 2
    assert "WORKLOG.md" in capsys.readouterr().err


def test_main_escape_hatch_bypasses_block(tmp_path: Path, monkeypatch) -> None:
    import os
    import time

    working = tmp_path / "working"
    working.mkdir()
    worklog = working / "WORKLOG.md"
    worklog.write_text("# Worklog")
    old_time = time.time() - 7200
    os.utime(worklog, (old_time, old_time))

    monkeypatch.setenv("CLD_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("CCCS_ALLOW_STALE_WORKLOG", "1")
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    assert worklog_guard.main() == 0
