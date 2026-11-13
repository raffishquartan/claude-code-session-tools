"""Tests for `ccst move-session`, which forwards its arguments unchanged to the
bundled move-session script under the interpreter that has cc-session-tools
installed."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from cc_session_tools.cli import ccst

UUID = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"


def _env(home: Path, tmp_path: Path, root: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_PROJECT_DIR")}
    env.update(
        HOME=str(home),
        CCST_NO_AUTO_SYNC="1",
        CCST_SESSIONS_DIR=str(tmp_path / "db"),
        CLAUDE_SESSION_TOOLS_REPO_ROOT=str(root),
    )
    env.pop("CLAUDE_SESSION_TOOLS_PROJ_ROOT", None)
    return env


def _ccst(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def synthetic(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """An idle session `proj/cc-sessions/20260503-old-tag` with a one-record
    transcript under a scratch HOME. Returns (session dir, env)."""
    home = tmp_path / "home"
    root = tmp_path / "projects-root"
    cwd = root / "proj"
    session_dir = cwd / "cc-sessions" / "20260503-old-tag"
    session_dir.mkdir(parents=True)
    key_dir = home / ".claude" / "projects" / str(cwd).replace("/", "-")
    key_dir.mkdir(parents=True)
    jsonl = key_dir / f"{UUID}.jsonl"
    jsonl.write_text(json.dumps({
        "type": "user", "uuid": "u1", "timestamp": "2026-05-03T13:00:00.000Z",
        "cwd": str(cwd), "sessionId": UUID,
        "message": {"role": "user", "content": "hello"},
    }) + "\n")
    idle = time.time() - 600
    os.utime(jsonl, (idle, idle))
    return session_dir, _env(home, tmp_path, root)


def test_forwards_arguments_that_start_with_an_option(
    synthetic: tuple[Path, dict[str, str]],
) -> None:
    session_dir, env = synthetic
    result = _ccst(
        "move-session", "--src-session", str(session_dir), "--rename-tag", "20260503-new-tag",
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PLAN (RENAME)" in result.stdout
    assert "DRY-RUN" in result.stdout


def test_help_is_the_script_usage(synthetic: tuple[Path, dict[str, str]]) -> None:
    _, env = synthetic
    result = _ccst("move-session", "--help", env=env)
    assert result.returncode == 0, result.stderr
    assert "--src-session" in result.stdout
    assert "--rename-tag" in result.stdout


def test_exit_code_passes_through(synthetic: tuple[Path, dict[str, str]]) -> None:
    _, env = synthetic
    result = _ccst("move-session", "--rename-tag", "20260503-new-tag", env=env)
    assert result.returncode == 2
    assert "--src-session" in result.stderr


def test_listed_in_ccst_help(synthetic: tuple[Path, dict[str, str]]) -> None:
    _, env = synthetic
    result = _ccst("--help", env=env)
    assert "move-session" in result.stdout


def test_runs_install_sync_before_forwarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """move-session is not an exempt verb: the first call after an upgrade
    syncs ~/.claude like any other ordinary command."""
    from cc_session_tools.lib import install_sync

    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    ensure = mocker.patch.object(install_sync, "ensure_synced")
    run = mocker.patch.object(
        ccst.subprocess, "run", return_value=subprocess.CompletedProcess([], 3)
    )
    monkeypatch.setattr(ccst.sys, "argv", ["ccst", "move-session", "--src-session", "x"])

    with pytest.raises(SystemExit) as exc:
        ccst.main()

    assert exc.value.code == 3
    ensure.assert_called_once_with(
        noun="move-session", verb=None, installed_version=ccst.__version__
    )
    cmd = run.call_args.args[0]
    assert cmd[0] == sys.executable
    assert cmd[1].endswith("skills/move-session/scripts/move_session.py")
    assert cmd[2:] == ["--src-session", "x"]


@pytest.mark.parametrize("signum", [2, 9, 15])
def test_signal_killed_script_exits_128_plus_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture, signum: int
) -> None:
    """A child killed by signal n has returncode -n; the shell convention for
    that is 128 + n."""
    from cc_session_tools.lib import install_sync

    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    mocker.patch.object(install_sync, "ensure_synced")
    mocker.patch.object(
        ccst.subprocess, "run", return_value=subprocess.CompletedProcess([], -signum)
    )
    assert ccst._cmd_move_session(["--src-session", "x"]) == 128 + signum
