from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.cli import ccd


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_launch(monkeypatch):
    captured: dict = {}

    def fake_launch(cmd, env):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env)

    monkeypatch.setattr(ccd, "launch_claude", fake_launch)
    return captured


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _set_repo_root(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(path))


def _set_proj_root(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(path))


def _make_valid_project(tmp_path: Path, monkeypatch) -> Path:
    """Return a project dir under a repo root; set env vars."""
    repos = tmp_path / "repos"
    proj = repos / "myproj"
    proj.mkdir(parents=True)
    _set_repo_root(monkeypatch, repos)
    return proj


# ---------------------------------------------------------------------------
# Test 1: dry-run prints YAML report, exits 0, launch_claude NOT called
# ---------------------------------------------------------------------------


def test_dry_run_prints_report_exits_0_no_launch(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    proj = _make_valid_project(tmp_path, monkeypatch)
    monkeypatch.chdir(proj)

    rc = ccd.main(["--dry-run", "foo"])
    assert rc == 0

    # launch_claude must not have been called
    assert captured_launch == {}

    out = capsys.readouterr().out
    assert "ccd dry-run:" in out
    assert "cwd:" in out
    assert "tag:" in out
    assert "session_name:" in out
    assert "session_dir:" in out


# ---------------------------------------------------------------------------
# Test 2: session directory is NOT created on disk
# ---------------------------------------------------------------------------


def test_dry_run_does_not_create_session_dir(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    proj = _make_valid_project(tmp_path, monkeypatch)
    monkeypatch.chdir(proj)

    rc = ccd.main(["--dry-run", "foo"])
    assert rc == 0

    # No cc-sessions directory should exist at all
    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d")
    expected_dir = proj / "cc-sessions" / f"{date_str}-foo"
    assert not expected_dir.exists()
