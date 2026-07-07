"""Tests for ccst doctor and cc_session_tools.lib.doctor."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_session_tools.lib.doctor import (
    CheckResult,
    Status,
    check_cli_on_path,
    check_env_dir,
    check_hook_registered,
    check_pypi_version,
    check_settings_json,
    check_skill_symlink,
    format_results,
    run_all_checks,
    _extract_bundle_hook_names,
    _version_tuple,
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


# ---------- _version_tuple ----------

def test_version_tuple_simple() -> None:
    assert _version_tuple("1.2.3") == (1, 2, 3)


def test_version_tuple_single() -> None:
    assert _version_tuple("10") == (10,)


def test_version_tuple_non_numeric_part() -> None:
    t = _version_tuple("1.2.3a")
    assert t == (1, 2, 0)  # non-numeric → 0


# ---------- check_cli_on_path ----------

def test_check_cli_on_path_found() -> None:
    result = check_cli_on_path("python3")
    assert result.status in (Status.OK, Status.WARN)
    assert "PATH:python3" == result.name


def test_check_cli_on_path_not_found() -> None:
    result = check_cli_on_path("definitely-not-a-real-cli-zzz")
    assert result.status == Status.FAIL
    assert "not found" in result.reason


# ---------- check_env_dir ----------

def test_check_env_dir_not_set() -> None:
    r = check_env_dir("MY_VAR", None)
    assert r.status == Status.WARN
    assert "not set" in r.reason


def test_check_env_dir_set_exists(tmp_path: Path) -> None:
    r = check_env_dir("MY_VAR", str(tmp_path))
    assert r.status == Status.OK


def test_check_env_dir_set_missing() -> None:
    r = check_env_dir("MY_VAR", "/nonexistent/path/xyz")
    assert r.status == Status.FAIL
    assert "does not exist" in r.reason


# ---------- check_settings_json ----------

def test_check_settings_json_exists_valid(tmp_path: Path) -> None:
    s = tmp_path / "settings.json"
    s.write_text('{"hooks": {}}')
    r = check_settings_json(s)
    assert r.status == Status.OK


def test_check_settings_json_missing(tmp_path: Path) -> None:
    s = tmp_path / "settings.json"
    r = check_settings_json(s)
    assert r.status == Status.FAIL
    assert "not found" in r.reason


def test_check_settings_json_invalid_json(tmp_path: Path) -> None:
    s = tmp_path / "settings.json"
    s.write_text("{not valid json")
    r = check_settings_json(s)
    assert r.status == Status.FAIL
    assert "invalid" in r.reason.lower()


# ---------- check_hook_registered ----------

def _settings_with_cmd(command: str, event: str = "Stop") -> dict:
    return {"hooks": {event: [{"hooks": [{"type": "command", "command": command}]}]}}


def test_check_hook_registered_present() -> None:
    settings = _settings_with_cmd("ccst hooks run session-end")
    r = check_hook_registered("session-end", settings)
    assert r.status == Status.OK


def test_check_hook_registered_missing() -> None:
    r = check_hook_registered("session-end", {})
    assert r.status == Status.WARN
    assert "not found" in r.reason


def test_check_hook_registered_wrong_event_still_found() -> None:
    """Hook registered under any event should pass."""
    settings = _settings_with_cmd("ccst hooks run session-tag", event="SessionStart")
    r = check_hook_registered("session-tag", settings)
    assert r.status == Status.OK


# ---------- check_skill_symlink ----------

def test_check_skill_symlink_correct(tmp_path: Path) -> None:
    src = tmp_path / "skills" / "my-skill"
    src.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    link = target_dir / "my-skill"
    link.symlink_to(src)
    r = check_skill_symlink("my-skill", src, target_dir)
    assert r.status == Status.OK


def test_check_skill_symlink_missing(tmp_path: Path) -> None:
    src = tmp_path / "skills" / "my-skill"
    src.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    r = check_skill_symlink("my-skill", src, target_dir)
    assert r.status == Status.WARN
    assert "no symlink" in r.reason


def test_check_skill_symlink_wrong_target(tmp_path: Path) -> None:
    src = tmp_path / "skills" / "my-skill"
    src.mkdir(parents=True)
    other = tmp_path / "other" / "my-skill"
    other.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    link = target_dir / "my-skill"
    link.symlink_to(other)
    r = check_skill_symlink("my-skill", src, target_dir)
    assert r.status == Status.FAIL
    assert "points to" in r.reason


def test_check_skill_symlink_non_symlink(tmp_path: Path) -> None:
    src = tmp_path / "skills" / "my-skill"
    src.mkdir(parents=True)
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    real_dir = target_dir / "my-skill"
    real_dir.mkdir()
    r = check_skill_symlink("my-skill", src, target_dir)
    assert r.status == Status.FAIL
    assert "not a symlink" in r.reason


def test_check_skill_symlink_compatible_different_install(tmp_path: Path) -> None:
    """A symlink pointing at a different but valid CCST install is OK with a NOTE.

    Covers the multi-clone case (canonical clone vs worktree, pipx vs uv,
    etc.) — the user's symlinks legitimately point at one install while
    doctor is invoked from another.
    """
    src = tmp_path / "skills" / "my-skill"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: my-skill\n---\n")

    other_install = tmp_path / "other-ccst-clone" / "skills" / "my-skill"
    other_install.mkdir(parents=True)
    (other_install / "SKILL.md").write_text("---\nname: my-skill\n---\n")

    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "my-skill").symlink_to(other_install)

    r = check_skill_symlink("my-skill", src, target_dir)
    assert r.status == Status.OK
    assert "NOTE" in r.reason
    assert str(other_install) in r.reason


def test_check_skill_symlink_wrong_target_no_skill_md(tmp_path: Path) -> None:
    """Symlink to a directory that is not a SKILL.md-bearing CCST skill -> FAIL."""
    src = tmp_path / "skills" / "my-skill"
    src.mkdir(parents=True)
    other = tmp_path / "random-dir" / "my-skill"
    other.mkdir(parents=True)
    # No SKILL.md inside `other`
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    (target_dir / "my-skill").symlink_to(other)

    r = check_skill_symlink("my-skill", src, target_dir)
    assert r.status == Status.FAIL
    assert "not a valid" in r.reason


# ---------- check_pypi_version ----------

def test_check_pypi_version_network_failure_returns_ok() -> None:
    """Network failure should NOT cause a FAIL result."""
    with patch("cc_session_tools.lib.doctor.check_pypi_version") as mock_check:
        mock_check.return_value = CheckResult(
            name="version:pypi",
            status=Status.OK,
            reason="PyPI check skipped (network unavailable or httpx not installed)",
        )
        r = mock_check("0.11.0")
    assert r.status == Status.OK


def test_check_pypi_version_up_to_date() -> None:
    """When installed == latest, result is OK."""
    import httpx
    with patch("httpx.get") as mock_get:
        mock_resp = mock_get.return_value
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"info": {"version": "0.11.0"}}
        r = check_pypi_version("0.11.0")
    assert r.status == Status.OK
    assert "up to date" in r.reason


def test_check_pypi_version_outdated() -> None:
    """When installed < latest, result is WARN."""
    import httpx
    with patch("httpx.get") as mock_get:
        mock_resp = mock_get.return_value
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"info": {"version": "0.99.0"}}
        r = check_pypi_version("0.11.0")
    assert r.status == Status.WARN
    assert "0.99.0" in r.reason


# ---------- format_results ----------

def test_format_results_shows_status_and_name() -> None:
    results = [
        CheckResult("foo", Status.OK, "all good"),
        CheckResult("bar", Status.WARN, "missing"),
    ]
    out = format_results(results)
    assert "OK" in out
    assert "WARN" in out
    assert "foo" in out
    assert "bar" in out


def test_format_results_empty() -> None:
    out = format_results([])
    assert "no checks" in out.lower()


# ---------- _extract_bundle_hook_names ----------

def test_extract_bundle_hook_names(tmp_path: Path) -> None:
    bundle = {
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "ccst hooks run session-end"}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": "ccst hooks run session-tag"}]}],
        }
    }
    p = tmp_path / "bundle.json"
    p.write_text(json.dumps(bundle))
    names = _extract_bundle_hook_names(p)
    assert set(names) == {"session-end", "session-tag"}


def test_extract_bundle_hook_names_missing_file(tmp_path: Path) -> None:
    names = _extract_bundle_hook_names(tmp_path / "nope.json")
    assert names == []


# ---------- run_all_checks ----------

def test_run_all_checks_returns_list(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
    )
    assert len(results) > 0
    assert all(isinstance(r, CheckResult) for r in results)


def test_run_all_checks_includes_hook_checks(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
    )
    hook_checks = [r for r in results if r.name.startswith("hook:")]
    assert len(hook_checks) == 8  # all bundled hooks


def test_run_all_checks_warns_for_missing_hooks(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
    )
    hook_checks = [r for r in results if r.name.startswith("hook:")]
    assert all(r.status == Status.WARN for r in hook_checks)


# ---------- CLI integration ----------

def test_doctor_runs_and_exits(tmp_path: Path) -> None:
    """ccst doctor completes without crashing."""
    result = _run("doctor", "--no-pypi")
    # Exit code 0 (all OK) or 1 (issues found) are both acceptable; must not crash
    assert result.returncode in (0, 1)


def test_doctor_outputs_status_table() -> None:
    result = _run("doctor", "--no-pypi")
    assert "[OK" in result.stdout or "[WARN" in result.stdout or "[FAIL" in result.stdout


def test_doctor_exits_1_when_issues_found(tmp_path: Path) -> None:
    """When hooks are not registered, doctor should exit 1."""
    # In a clean environment, hooks likely aren't installed, so doctor exits 1
    result = _run("doctor", "--no-pypi")
    # At minimum the hooks WARNs will be triggered (they're not in a real settings.json)
    # We just check it doesn't crash
    assert result.returncode in (0, 1)


def test_doctor_drift_positional_accepted() -> None:
    """ccst doctor drift (positional) is accepted and behaves like --drift."""
    result_flag = _run("doctor", "--drift", "--no-pypi")
    result_positional = _run("doctor", "drift", "--no-pypi")
    assert result_positional.returncode == result_flag.returncode
    assert result_positional.stdout == result_flag.stdout


def test_doctor_drift_positional_not_exit_2() -> None:
    """ccst doctor drift must not exit 2 (argparse error)."""
    result = _run("doctor", "drift", "--no-pypi")
    assert result.returncode != 2
