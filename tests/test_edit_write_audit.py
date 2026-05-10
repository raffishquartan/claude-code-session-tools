"""Tests for cccs_hooks.edit_write_audit."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cccs_hooks.edit_write_audit import audit_path, AuditResult


# ---------- audit_path: sensitive-path detection ----------

def test_env_file_is_sensitive() -> None:
    result = audit_path(Path("/home/user/.env"), repo_roots=[Path("/home/user/repos")])
    assert result.sensitive_warning is not None


def test_env_with_extension_is_sensitive() -> None:
    result = audit_path(Path("/home/user/.env.local"), repo_roots=[Path("/home/user/repos")])
    assert result.sensitive_warning is not None


def test_id_rsa_is_sensitive() -> None:
    result = audit_path(Path("/home/user/.ssh/id_rsa"), repo_roots=[Path("/home/user/repos")])
    assert result.sensitive_warning is not None


def test_id_ed25519_is_sensitive() -> None:
    result = audit_path(Path("/home/user/.ssh/id_ed25519"), repo_roots=[Path("/home/user/repos")])
    assert result.sensitive_warning is not None


def test_netrc_is_sensitive() -> None:
    result = audit_path(Path("/home/user/.netrc"), repo_roots=[Path("/home/user/repos")])
    assert result.sensitive_warning is not None


def test_credentials_json_is_sensitive() -> None:
    result = audit_path(Path("/home/user/credentials.json"), repo_roots=[Path("/home/user/repos")])
    assert result.sensitive_warning is not None


def test_secrets_file_is_sensitive() -> None:
    result = audit_path(Path("/home/user/secrets.yaml"), repo_roots=[Path("/home/user/repos")])
    assert result.sensitive_warning is not None


def test_normal_python_file_not_sensitive() -> None:
    result = audit_path(Path("/home/user/repos/myproject/main.py"), repo_roots=[Path("/home/user/repos")])
    assert result.sensitive_warning is None


# ---------- audit_path: out-of-repo detection ----------

def test_out_of_repo_warns(tmp_path: Path) -> None:
    repo_root = tmp_path / "repos"
    result = audit_path(tmp_path / "random" / "file.txt", repo_roots=[repo_root])
    assert result.out_of_repo_warning is not None


def test_inside_repo_no_out_of_repo_warning(tmp_path: Path) -> None:
    repo_root = tmp_path / "repos"
    result = audit_path(repo_root / "myproject" / "file.py", repo_roots=[repo_root])
    assert result.out_of_repo_warning is None


def test_multiple_repo_roots(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    onedrive = tmp_path / "OneDrive"
    result = audit_path(onedrive / "claude" / "file.md", repo_roots=[repos, onedrive])
    assert result.out_of_repo_warning is None


# ---------- audit_path: WORKLOG auto-stage ----------

def test_worklog_path_flagged(tmp_path: Path) -> None:
    worklog = tmp_path / "cc-sessions" / "20260510-test" / "working" / "WORKLOG.md"
    worklog.parent.mkdir(parents=True)
    worklog.touch()
    result = audit_path(worklog, repo_roots=[tmp_path])
    assert result.should_git_add is True


def test_non_worklog_not_flagged(tmp_path: Path) -> None:
    result = audit_path(tmp_path / "some" / "file.py", repo_roots=[tmp_path])
    assert result.should_git_add is False


# ---------- CLI entry point ----------

def _make_hook_input(tool: str, path: str) -> str:
    return json.dumps({
        "tool_name": tool,
        "tool_input": {"file_path": path},
        "session_id": "test-session",
        "cwd": "/tmp",
    })


def _run_module(hook_input: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cccs_hooks.edit_write_audit"],
        input=hook_input,
        capture_output=True,
        text=True,
    )


def test_cli_exits_0_on_normal_file(tmp_path: Path) -> None:
    hook_input = _make_hook_input("Write", str(tmp_path / "repos" / "file.py"))
    result = _run_module(hook_input)
    assert result.returncode == 0


def test_cli_exits_0_on_sensitive_file(tmp_path: Path) -> None:
    hook_input = _make_hook_input("Edit", str(tmp_path / ".env"))
    result = _run_module(hook_input)
    # Must always exit 0 (never block)
    assert result.returncode == 0
    # Must emit a warning on stderr
    assert "sensitive" in result.stderr.lower() or "⚠" in result.stderr


def test_cli_ignores_non_edit_write_tools() -> None:
    hook_input = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "session_id": "test",
        "cwd": "/tmp",
    })
    result = _run_module(hook_input)
    assert result.returncode == 0
    assert result.stderr == ""
