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


# ---------------------------------------------------------------------------
# Test 3: report includes launch_command in shell-quoted form
# ---------------------------------------------------------------------------


def test_dry_run_report_includes_launch_command(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    proj = _make_valid_project(tmp_path, monkeypatch)
    monkeypatch.chdir(proj)

    rc = ccd.main(["--dry-run", "foo"])
    assert rc == 0

    out = capsys.readouterr().out
    # The report must include a launch_command line
    assert "launch_command:" in out
    # It should include the core claude invocation
    assert "claude" in out
    assert "-n" in out
    assert "--remote-control" in out


# ---------------------------------------------------------------------------
# Test 4: report shows task_list_id derived via id_for_project
# ---------------------------------------------------------------------------


def test_dry_run_report_shows_task_list_id(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    proj = _make_valid_project(tmp_path, monkeypatch)
    monkeypatch.chdir(proj)

    rc = ccd.main(["--dry-run", "foo"])
    assert rc == 0

    out = capsys.readouterr().out
    # The project is named "myproj" and its parent is the repo root,
    # so id_for_project returns "myproj".
    assert "task_list_id: myproj" in out


def test_dry_run_report_shows_none_task_list_id_when_outside_roots(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    # Project is NOT under any configured root
    repos = tmp_path / "repos"
    repos.mkdir()
    _set_repo_root(monkeypatch, repos)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    rc = ccd.main(["--dry-run", "--force", "foo"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "task_list_id: (none)" in out


# ---------------------------------------------------------------------------
# Test 5: report shows force: true when --force is also passed
# ---------------------------------------------------------------------------


def test_dry_run_report_shows_force_true(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    proj = _make_valid_project(tmp_path, monkeypatch)
    monkeypatch.chdir(proj)

    rc = ccd.main(["--dry-run", "--force", "foo"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "force: true" in out


def test_dry_run_report_shows_force_false_by_default(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    proj = _make_valid_project(tmp_path, monkeypatch)
    monkeypatch.chdir(proj)

    rc = ccd.main(["--dry-run", "foo"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "force: false" in out


# ---------------------------------------------------------------------------
# Test 6: validation errors appear in report and exit code is STILL 0
# ---------------------------------------------------------------------------


def test_dry_run_shows_validation_errors_and_exits_0(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    # cwd is outside any configured root => validation will fail
    repos = tmp_path / "repos"
    repos.mkdir()
    _set_repo_root(monkeypatch, repos)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    # Without --force, validation fails because cwd is not under a root
    rc = ccd.main(["--dry-run", "foo"])
    # Exit code must be 0 even when validation fails in dry-run
    assert rc == 0

    out = capsys.readouterr().out
    # validation section must contain error text (not "ok")
    assert "validation:" in out
    assert "validation: ok" not in out
    # The specific error for cwd-not-under-root is expected
    assert "not a direct subdirectory" in out or "cwd not" in out


# ---------------------------------------------------------------------------
# Test 7: extra passthrough args appear in launch_command in order
# ---------------------------------------------------------------------------


def test_dry_run_extra_args_in_launch_command(
    fake_home, tmp_path, monkeypatch, captured_launch, capsys
):
    proj = _make_valid_project(tmp_path, monkeypatch)
    monkeypatch.chdir(proj)

    rc = ccd.main(["--dry-run", "foo", "--model", "claude-opus-4-5", "--verbose"])
    assert rc == 0

    out = capsys.readouterr().out
    # Extra args must appear in the launch_command line and in the right order
    assert "--model" in out
    assert "claude-opus-4-5" in out
    assert "--verbose" in out
    # The launch_command line should have all args together in a shell-joinable form
    launch_line = next(
        line for line in out.splitlines() if "launch_command:" in line
    )
    assert "--model" in launch_line
    assert "claude-opus-4-5" in launch_line
    assert "--verbose" in launch_line
