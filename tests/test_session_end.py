"""Tests for cccs_hooks.session_end."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cccs_hooks.session_end import check_uncommitted, check_worklog_stale


_WORKTREE = Path(__file__).parent.parent


# ---------- check_uncommitted ----------

def test_no_git_repo_returns_none(tmp_path: Path) -> None:
    result = check_uncommitted(tmp_path)
    assert result is None


def test_clean_repo_returns_none(tmp_path: Path) -> None:
    import subprocess as sp
    sp.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, capture_output=True)
    # Create and commit a feature branch
    sp.run(["git", "checkout", "-b", "f/test"], cwd=tmp_path, capture_output=True)
    result = check_uncommitted(tmp_path)
    assert result is None


def test_feature_branch_with_uncommitted_changes_returns_warning(tmp_path: Path) -> None:
    import subprocess as sp
    sp.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "checkout", "-b", "f/feature"], cwd=tmp_path, capture_output=True)
    (tmp_path / "file.py").write_text("x")
    result = check_uncommitted(tmp_path)
    assert result is not None
    assert "uncommitted" in result.lower() or "commit" in result.lower()


def test_main_branch_uncommitted_no_warning(tmp_path: Path) -> None:
    import subprocess as sp
    sp.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    sp.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, capture_output=True)
    (tmp_path / "file.py").write_text("x")
    result = check_uncommitted(tmp_path)
    assert result is None


# ---------- check_worklog_stale ----------

def test_no_worklog_returns_none(tmp_path: Path) -> None:
    result = check_worklog_stale(tmp_path)
    assert result is None


def test_recently_modified_worklog_returns_none(tmp_path: Path) -> None:
    sessions = tmp_path / "cc-sessions" / "20260510-test" / "working"
    sessions.mkdir(parents=True)
    worklog = sessions / "WORKLOG.md"
    worklog.write_text("# Worklog")
    # mtime is just now — should be fresh
    result = check_worklog_stale(tmp_path, stale_seconds=3600)
    assert result is None


def test_stale_worklog_returns_warning(tmp_path: Path) -> None:
    import os, time
    sessions = tmp_path / "cc-sessions" / "20260510-test" / "working"
    sessions.mkdir(parents=True)
    worklog = sessions / "WORKLOG.md"
    worklog.write_text("# Worklog")
    # Back-date the mtime by 2 hours
    old_time = time.time() - 7200
    os.utime(worklog, (old_time, old_time))
    result = check_worklog_stale(tmp_path, stale_seconds=3600)
    assert result is not None
    assert "worklog" in result.lower()
