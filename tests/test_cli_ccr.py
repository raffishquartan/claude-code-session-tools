from __future__ import annotations

import os
from pathlib import Path

import pytest

from cc_session_tools.cli import ccr


@pytest.fixture
def captured_launch(monkeypatch):
    captured: dict = {}

    def fake_launch(cmd, env, cwd=None):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env)
        captured["cwd"] = cwd

    monkeypatch.setattr(ccr, "launch_claude_resume", fake_launch)
    return captured


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def fake_repos(fake_home, tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    repos.mkdir()
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos))
    return repos


def _make_session(repos: Path, project: str, basename: str) -> Path:
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    (sess / "out").mkdir()
    return sess


def test_ccr_unique_match_launches_resume(fake_repos, captured_launch):
    _make_session(fake_repos, "myproj", "20260504-foo-bar")

    rc = ccr.main(["foo-bar"])
    assert rc == 0

    cmd = captured_launch["cmd"]
    assert cmd[0] == "claude"
    assert "--resume" in cmd
    assert "20260504-foo-bar" in cmd
    assert "--remote-control" in cmd


def test_ccr_sets_session_start_hook_env_for_resume(fake_repos, captured_launch):
    _make_session(fake_repos, "myproj", "20260504-foo-bar")
    rc = ccr.main(["foo-bar"])
    assert rc == 0

    env = captured_launch["env"]
    assert env["CLD_SESSION_TAG"] == "foo-bar"
    assert env["CLD_SESSION_MODE"] == "resume"
    assert env["CLD_SESSION_DIR"].endswith("cc-sessions/20260504-foo-bar")
    assert env["CLAUDE_CODE_TASK_LIST_ID"] == "myproj"


def test_ccr_no_match_returns_1(fake_repos, capsys, captured_launch):
    _make_session(fake_repos, "myproj", "20260504-foo")
    rc = ccr.main(["nope"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no sessions match" in err


def test_ccr_multi_match_lists_and_returns_0(fake_repos, capsys, captured_launch):
    _make_session(fake_repos, "myproj", "20260504-foo-one")
    _make_session(fake_repos, "myproj", "20260503-foo-two")
    rc = ccr.main(["foo"])
    assert rc == 0
    # Should not have launched claude
    assert "cmd" not in captured_launch
    out = capsys.readouterr().out
    assert "20260504-foo-one" in out
    assert "20260503-foo-two" in out


def test_ccr_changes_to_project_dir_before_launch(fake_repos, captured_launch):
    sess = _make_session(fake_repos, "myproj", "20260504-foo")
    project_dir = sess.parent.parent
    rc = ccr.main(["foo"])
    assert rc == 0
    assert captured_launch["cwd"] == project_dir


# ---------------------------------------------------------------------------
# Task 13: exact-match fast-path
# ---------------------------------------------------------------------------

def test_ccr_exact_basename_skips_substring_ambiguity(fake_repos, captured_launch):
    # "20260504-foo" is an exact basename but also a substring of "20260504-foo-bar"
    _make_session(fake_repos, "proj1", "20260504-foo")
    _make_session(fake_repos, "proj2", "20260504-foo-bar")

    rc = ccr.main(["20260504-foo"])
    assert rc == 0
    assert "20260504-foo" in captured_launch["cmd"]
    assert "20260504-foo-bar" not in captured_launch["cmd"]


def test_ccr_falls_back_to_substring_when_no_exact_match(fake_repos, captured_launch):
    _make_session(fake_repos, "proj1", "20260504-improve-ccx")

    rc = ccr.main(["improve"])
    assert rc == 0
    assert "20260504-improve-ccx" in captured_launch["cmd"]


# ---------------------------------------------------------------------------
# Task 14: PATH check for claude binary
# ---------------------------------------------------------------------------

def test_ccr_fails_clearly_when_claude_not_on_path(fake_repos, monkeypatch, capsys):
    _make_session(fake_repos, "proj1", "20260504-foo")
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)

    rc = ccr.main(["foo"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "claude" in err.lower()
    assert ("not found" in err.lower() or "path" in err.lower())
