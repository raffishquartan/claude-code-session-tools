"""Tests for ccst doctor and cc_session_tools.lib.doctor."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_session_tools.lib.doctor import (
    CheckResult,
    LegacyMigrationPaths,
    Status,
    check_ccsched_job_registered,
    check_cli_on_path,
    check_data_stores,
    check_env_dir,
    check_hook_registered,
    check_install_everything_synced,
    check_no_stale_hooks,
    check_pending_data_store_migration,
    check_pending_pdata_migration,
    check_pypi_version,
    check_settings_json,
    check_skill_symlink,
    format_results,
    run_all_checks,
    _extract_bundle_hook_names,
    _version_tuple,
)
from cc_session_tools.lib import db as _db
from cc_session_tools.lib import telemetry_store
from cccs_hooks.telemetry import TelemetryEntry, log_event


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
    settings = _settings_with_cmd("ccst hooks run after-response")
    r = check_hook_registered("after-response", settings)
    assert r.status == Status.OK


def test_check_hook_registered_missing() -> None:
    r = check_hook_registered("after-response", {})
    assert r.status == Status.WARN
    assert "not found" in r.reason


def test_check_hook_registered_wrong_event_still_found() -> None:
    """Hook registered under any event should pass."""
    settings = _settings_with_cmd("ccst hooks run session-tag", event="SessionStart")
    r = check_hook_registered("session-tag", settings)
    assert r.status == Status.OK


# ---------- check_no_stale_hooks ----------

def test_check_no_stale_hooks_clean_settings_is_ok() -> None:
    settings = _settings_with_cmd("ccst hooks run after-response")
    results = check_no_stale_hooks(settings)
    assert [r.status for r in results] == [Status.OK]


@pytest.mark.parametrize("removed_hook", ["edit-write-audit", "prompt-guard", "session-end"])
def test_check_no_stale_hooks_fails_for_a_removed_hook(removed_hook: str) -> None:
    """FAIL, not WARN: the entry breaks every event it is bound to, which is
    a broken session rather than a missing feature."""
    settings = _settings_with_cmd(f"ccst hooks run {removed_hook}")
    results = check_no_stale_hooks(settings)
    assert [r.status for r in results] == [Status.FAIL]
    assert removed_hook in results[0].name
    assert f"ccst hooks uninstall --hook {removed_hook} --apply" in results[0].reason


def test_check_no_stale_hooks_reports_every_stale_entry() -> None:
    settings = {
        "hooks": {
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "ccst hooks run prompt-guard"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "ccst hooks run session-end"}]}],
        }
    }
    results = check_no_stale_hooks(settings)
    assert {r.name for r in results} == {"hooks:stale:prompt-guard", "hooks:stale:session-end"}


def test_check_no_stale_hooks_ignores_foreign_commands() -> None:
    settings = _settings_with_cmd("/opt/mytool/notify.sh")
    results = check_no_stale_hooks(settings)
    assert [r.status for r in results] == [Status.OK]


def test_run_all_checks_fails_on_a_stale_hook_entry(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(_settings_with_cmd("ccst hooks run prompt-guard")))
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"
    results = run_all_checks(
        installed_version="1.4.1",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
    )
    stale = [r for r in results if r.name.startswith("hooks:stale:")]
    assert [r.status for r in stale] == [Status.FAIL]


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


# ---------- check_install_everything_synced ----------

def test_check_install_synced_ok_when_versions_match() -> None:
    result = check_install_everything_synced(
        installed_version="2.4.0", synced_version="2.4.0"
    )
    assert result.status == Status.OK


def test_check_install_synced_warns_when_never_synced() -> None:
    result = check_install_everything_synced(
        installed_version="2.4.0", synced_version=None
    )
    assert result.status == Status.WARN
    assert "install-everything" in result.reason


def test_check_install_synced_warns_when_stale() -> None:
    result = check_install_everything_synced(
        installed_version="2.4.0", synced_version="2.3.0"
    )
    assert result.status == Status.WARN
    assert "2.3.0" in result.reason
    assert "2.4.0" in result.reason


# ---------- format_results ----------

def test_format_results_shows_status_and_name() -> None:
    """Default (show_all=False) shows only non-OK results."""
    results = [
        CheckResult("foo", Status.OK, "all good"),
        CheckResult("bar", Status.WARN, "missing"),
    ]
    out = format_results(results)
    assert "WARN" in out
    assert "bar" in out
    assert "foo" not in out


def test_format_results_show_all_includes_ok() -> None:
    results = [
        CheckResult("foo", Status.OK, "all good"),
        CheckResult("bar", Status.WARN, "missing"),
    ]
    out = format_results(results, show_all=True)
    assert "OK" in out
    assert "WARN" in out
    assert "foo" in out
    assert "bar" in out


def test_format_results_default_prints_all_argument_hint() -> None:
    results = [CheckResult("bar", Status.WARN, "missing")]
    out = format_results(results)
    assert "--all" in out


def test_format_results_show_all_omits_all_argument_hint() -> None:
    results = [CheckResult("bar", Status.WARN, "missing")]
    out = format_results(results, show_all=True)
    assert "--all" not in out


def test_format_results_default_all_ok_prints_summary_not_full_list() -> None:
    """No non-OK results and show_all=False: nothing to filter down to, so a
    summary line stands in for the (empty) table rather than printing
    nothing at all."""
    results = [
        CheckResult("foo", Status.OK, "all good"),
        CheckResult("baz", Status.OK, "also good"),
    ]
    out = format_results(results)
    assert "foo" not in out
    assert "baz" not in out
    assert "2" in out  # some indication of how many checks ran/passed
    assert "--all" in out


def test_format_results_empty() -> None:
    out = format_results([])
    assert "no checks" in out.lower()


# ---------- _extract_bundle_hook_names ----------

def test_extract_bundle_hook_names(tmp_path: Path) -> None:
    bundle = {
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "ccst hooks run after-response"}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": "ccst hooks run session-tag"}]}],
        }
    }
    p = tmp_path / "bundle.json"
    p.write_text(json.dumps(bundle))
    names = _extract_bundle_hook_names(p)
    assert set(names) == {"after-response", "session-tag"}


def test_extract_bundle_hook_names_missing_file(tmp_path: Path) -> None:
    names = _extract_bundle_hook_names(tmp_path / "nope.json")
    assert names == []


# ---------- run_all_checks ----------

def test_run_all_checks_returns_list(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"
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
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"
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
    assert len(hook_checks) == 11  # all bundled hooks


def test_run_all_checks_warns_for_missing_hooks(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"
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


def test_doctor_default_omits_ok_and_shows_all_hint() -> None:
    """Confirms the --all wiring end-to-end (not just format_results() in
    isolation): default output has no [OK ] lines and does point at --all."""
    result = _run("doctor", "--no-pypi")
    assert "[OK  ]" not in result.stdout
    assert "--all" in result.stdout


def test_doctor_all_flag_omits_the_all_hint() -> None:
    """--all's own output doesn't need to be told about --all again. (Whether
    any individual check happens to be OK on the machine running this test
    isn't asserted here - that's covered deterministically by the
    format_results()-level tests above via synthetic CheckResults.)"""
    result = _run("doctor", "--no-pypi", "--all")
    assert "--all" not in result.stdout


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


# ---------- mute / unmute / list-mutes CLI (net new — zero coverage existed) ----------

def test_mute_writes_and_list_mutes_shows_it(tmp_path: Path) -> None:
    mutes_file = tmp_path / "sessions.db"
    r1 = _run("doctor", "--mute", "version:pypi", "--mutes-file", str(mutes_file))
    assert r1.returncode == 0
    assert "Muted 'version:pypi'" in r1.stdout

    r2 = _run("doctor", "--list-mutes", "--mutes-file", str(mutes_file))
    assert r2.returncode == 0
    assert "version:pypi" in r2.stdout


def test_list_mutes_empty_reports_none(tmp_path: Path) -> None:
    mutes_file = tmp_path / "sessions.db"
    r = _run("doctor", "--list-mutes", "--mutes-file", str(mutes_file))
    assert r.returncode == 0
    assert "No checks are muted" in r.stdout


def test_unmute_removes_a_muted_check(tmp_path: Path) -> None:
    mutes_file = tmp_path / "sessions.db"
    _run("doctor", "--mute", "hook:foo", "--mutes-file", str(mutes_file))
    r = _run("doctor", "--unmute", "hook:foo", "--mutes-file", str(mutes_file))
    assert r.returncode == 0
    assert "Un-muted 'hook:foo'" in r.stdout

    r2 = _run("doctor", "--list-mutes", "--mutes-file", str(mutes_file))
    assert "hook:foo" not in r2.stdout


def test_unmute_not_muted_returns_1(tmp_path: Path) -> None:
    mutes_file = tmp_path / "sessions.db"
    r = _run("doctor", "--unmute", "never-muted", "--mutes-file", str(mutes_file))
    assert r.returncode == 1
    assert "was not muted" in r.stdout


def test_drift_mode_hides_muted_issues(tmp_path: Path) -> None:
    mutes_file = tmp_path / "sessions.db"
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    # Mute one of the checks that will definitely WARN/FAIL in a clean env.
    r_first = _run("doctor", "--drift", "--no-pypi", "--settings", str(settings))
    # Extract a real un-muted check name from the drift output to mute it.
    lines = [l for l in r_first.stdout.splitlines() if l.strip().startswith("[")]
    assert lines, "expected at least one un-muted issue to mute in this test"
    name = lines[0].split("]", 1)[1].split()[0]

    _run("doctor", "--mute", name, "--mutes-file", str(mutes_file))
    r_after = _run(
        "doctor", "--drift", "--no-pypi", "--settings", str(settings),
        "--mutes-file", str(mutes_file),
    )
    assert name not in r_after.stdout


def test_mutes_file_default_is_sessions_db_not_json(tmp_path: Path, monkeypatch) -> None:
    """Regression guard for D7: the default mute store is sessions.db, not
    the old ~/.claude/cc-doctor-mutes.json path."""
    from cc_session_tools.lib import doctor_mutes, sessions_db
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    assert doctor_mutes.default_mutes_path() == sessions_db.default_db_path()
    assert doctor_mutes.default_mutes_path().name == "sessions.db"


# ---------- check_data_stores ----------

_DDL = "CREATE TABLE IF NOT EXISTS widgets (id INTEGER PRIMARY KEY);"


def test_check_data_stores_ok_for_valid_existing_db(tmp_path: Path) -> None:
    target = tmp_path / "ccmsg.db"
    _db.connect(target, ddl=_DDL).close()

    results = check_data_stores({"ccmsg": target})

    assert len(results) == 1
    assert results[0].name == "data-store:ccmsg"
    assert results[0].status == Status.OK


def test_check_data_stores_ok_for_valid_existing_json(tmp_path: Path) -> None:
    target = tmp_path / "claude-flags.json"
    target.write_text('{"mtime": 1.0, "path": "/x", "flags": []}')

    results = check_data_stores({"claude-flags": target})

    assert results[0].status == Status.OK


def test_check_data_stores_fail_for_corrupt_db(tmp_path: Path) -> None:
    target = tmp_path / "ccsched.db"
    target.write_bytes(b"not a sqlite file at all")

    results = check_data_stores({"ccsched": target})

    assert results[0].status == Status.FAIL
    assert "ccsched.db" in results[0].reason


def test_check_data_stores_fail_for_corrupt_json(tmp_path: Path) -> None:
    target = tmp_path / "claude-flags.json"
    target.write_text("{not valid json")

    results = check_data_stores({"claude-flags": target})

    assert results[0].status == Status.FAIL


def test_check_data_stores_warn_when_missing_but_parent_writable(tmp_path: Path) -> None:
    target = tmp_path / "not-created-yet" / "sessions.db"

    results = check_data_stores({"sessions": target})

    assert results[0].status == Status.WARN
    assert "will be created" in results[0].reason


def test_check_data_stores_fail_when_missing_and_ancestor_unwritable(tmp_path: Path) -> None:
    readonly_root = tmp_path / "readonly"
    readonly_root.mkdir()
    readonly_root.chmod(0o500)
    target = readonly_root / "telemetry.db"
    try:
        results = check_data_stores({"telemetry": target})
        assert results[0].status == Status.FAIL
    finally:
        readonly_root.chmod(0o700)  # allow tmp_path cleanup


def test_check_data_stores_handles_multiple_stores_independently(tmp_path: Path) -> None:
    good = tmp_path / "ccmsg.db"
    _db.connect(good, ddl=_DDL).close()
    bad = tmp_path / "ccsched.db"
    bad.write_bytes(b"garbage")

    results = check_data_stores({"ccmsg": good, "ccsched": bad})

    by_name = {r.name: r for r in results}
    assert by_name["data-store:ccmsg"].status == Status.OK
    assert by_name["data-store:ccsched"].status == Status.FAIL


# ---------- run_all_checks wiring ----------

def test_run_all_checks_includes_data_store_checks_when_store_paths_given(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"
    store = tmp_path / "ccmsg.db"
    _db.connect(store, ddl=_DDL).close()

    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
        store_paths={"ccmsg": store},
    )

    assert any(r.name == "data-store:ccmsg" for r in results)


def test_run_all_checks_includes_install_synced_check(tmp_path: Path) -> None:
    """Found during final review: no existing test asserted the install:synced
    check actually reaches run_all_checks()'s results, or that the
    synced_version argument threads through correctly - the same "unit-tested
    in isolation, never proven wired into the real pipeline" gap this repo's
    own uv-aware-command-cache plan warns about."""
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"

    results = run_all_checks(
        installed_version="2.4.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
        synced_version="2.3.0",
    )

    matches = [r for r in results if r.name == "install:synced"]
    assert len(matches) == 1
    assert matches[0].status == Status.WARN
    assert "2.3.0" in matches[0].reason
    assert "2.4.0" in matches[0].reason


def test_cmd_doctor_passes_synced_version_from_install_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirms the _cmd_doctor -> run_all_checks wiring specifically (not
    just run_all_checks in isolation): a value actually recorded via
    install_sync.record_synced() shows up as an OK install:synced result in
    `ccst doctor`'s real subprocess output."""
    from cc_session_tools import __version__ as version

    env = os.environ.copy()
    env["CCST_DATA_HOME"] = str(tmp_path / "data-home")
    from cc_session_tools.lib import install_sync
    install_sync.record_synced(version, path=tmp_path / "data-home" / "sessions.db")

    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "doctor", "--no-pypi"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent), env=env,
    )

    assert "[OK  ] install:synced" in result.stdout


def test_run_all_checks_skips_data_store_checks_when_omitted(tmp_path: Path) -> None:
    """store_paths defaults to None — existing callers that don't pass it are unaffected."""
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"

    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
    )

    assert not any(r.name.startswith("data-store:") for r in results)


def test_doctor_output_includes_data_store_checks() -> None:
    result = _run("doctor", "--no-pypi")
    assert "data-store:" in result.stdout


# ---------- check_pending_data_store_migration ----------


def _legacy_paths(tmp_path: Path) -> LegacyMigrationPaths:
    return LegacyMigrationPaths(
        ccmsg_old_root=tmp_path / "cc-messages",
        ccsched_old_dir=tmp_path / "cc-scheduler",
        tags_dir=tmp_path / "session-tags",
        mutes_file=tmp_path / "cc-doctor-mutes.json",
        telemetry_old_dir=tmp_path / "logs",
        data_home=tmp_path / "data-home",
    )


def test_check_pending_migration_ok_when_nothing_legacy(tmp_path: Path) -> None:
    results = check_pending_data_store_migration(_legacy_paths(tmp_path))

    assert {r.name for r in results} == {
        "migration-to-1.0.0:ccmsg", "migration-to-1.0.0:ccsched", "migration-to-1.0.0:sessions", "migration-to-1.0.0:telemetry",
    }
    assert all(r.status == Status.OK for r in results)
    assert all("nothing to migrate" in r.reason for r in results)


def test_check_pending_migration_fails_for_unmigrated_ccmsg(tmp_path: Path) -> None:
    paths = _legacy_paths(tmp_path)
    (paths.ccmsg_old_root / "projects" / "alpha" / "inbox").mkdir(parents=True)
    (paths.ccmsg_old_root / "projects" / "alpha" / "inbox" / "msg.md").write_text("x")

    results = {r.name: r for r in check_pending_data_store_migration(paths)}

    assert results["migration-to-1.0.0:ccmsg"].status == Status.FAIL
    assert "ccst migrate all" in results["migration-to-1.0.0:ccmsg"].reason
    assert results["migration-to-1.0.0:ccsched"].status == Status.OK


def test_check_pending_migration_fails_for_unmigrated_ccsched(tmp_path: Path) -> None:
    paths = _legacy_paths(tmp_path)
    paths.ccsched_old_dir.mkdir(parents=True)
    (paths.ccsched_old_dir / "jobs.toml").write_text("[[job]]\n")

    results = {r.name: r for r in check_pending_data_store_migration(paths)}

    assert results["migration-to-1.0.0:ccsched"].status == Status.FAIL


def test_check_pending_migration_fails_for_unmigrated_session_tags(tmp_path: Path) -> None:
    paths = _legacy_paths(tmp_path)
    paths.tags_dir.mkdir(parents=True)
    (paths.tags_dir / "abc-123.tag").write_text("my-session")

    results = {r.name: r for r in check_pending_data_store_migration(paths)}

    assert results["migration-to-1.0.0:sessions"].status == Status.FAIL


def test_check_pending_migration_fails_for_unmigrated_doctor_mutes(tmp_path: Path) -> None:
    paths = _legacy_paths(tmp_path)
    paths.mutes_file.write_text(json.dumps({"some-check": "2026-01-01"}))

    results = {r.name: r for r in check_pending_data_store_migration(paths)}

    assert results["migration-to-1.0.0:sessions"].status == Status.FAIL


def test_check_pending_migration_survives_a_corrupt_new_store(tmp_path: Path) -> None:
    """Found during install-sync-nudge's final review: sqlite3.connect()
    opens lazily and doesn't validate the file, so a corrupt sessions.db
    only fails on the first SELECT (sqlite3.DatabaseError), not on connect()
    (sqlite3.OperationalError) - the row-counting loop's inner except only
    caught the latter, so a corrupt new store crashed this whole check
    instead of degrading to "can't be opened -> 0 rows", matching every
    other unreadable-store case this function already handles."""
    paths = _legacy_paths(tmp_path)
    paths.tags_dir.mkdir(parents=True)
    (paths.tags_dir / "abc-123.tag").write_text("my-session")  # legacy data present
    paths.data_home.mkdir(parents=True)
    (paths.data_home / "sessions.db").write_bytes(b"not a sqlite database file")

    results = {r.name: r for r in check_pending_data_store_migration(paths)}

    # legacy data present + new store unreadable (treated as 0 rows) -> FAIL,
    # same as the "migration not yet run" case - the check must not raise.
    assert results["migration-to-1.0.0:sessions"].status == Status.FAIL


def test_check_pending_migration_fails_for_unmigrated_telemetry(tmp_path: Path) -> None:
    paths = _legacy_paths(tmp_path)
    paths.telemetry_old_dir.mkdir(parents=True)
    (paths.telemetry_old_dir / "fires.jsonl").write_text('{"hook": "x"}\n')

    results = {r.name: r for r in check_pending_data_store_migration(paths)}

    assert results["migration-to-1.0.0:telemetry"].status == Status.FAIL


def test_telemetry_still_fails_when_hooks_have_filled_the_db_but_no_import_ran(
    tmp_path: Path,
) -> None:
    """The silent half of the bug. The hook writer fills telemetry.db from the
    first session after install, so a row-count test read that as 'already
    migrated' and downgraded FAIL to WARN — and because the SessionStart hook
    only surfaces FAILs, the operator was never told the import was still
    outstanding."""
    paths = _legacy_paths(tmp_path)
    paths.telemetry_old_dir.mkdir(parents=True)
    (paths.telemetry_old_dir / "fires.jsonl").write_text('{"hook": "x"}\n')
    log_event(
        TelemetryEntry(
            hook="session-tag", event="SessionStart", tool="", session_id="s",
            cwd_short="x", decision="allow", cache="none", verdict="ok", input_hash="",
        ),
        hooks_dir=paths.data_home,
    )

    results = {r.name: r for r in check_pending_data_store_migration(paths)}

    assert results["migration-to-1.0.0:telemetry"].status == Status.FAIL


def test_telemetry_warns_once_the_import_marker_is_recorded(tmp_path: Path) -> None:
    paths = _legacy_paths(tmp_path)
    paths.telemetry_old_dir.mkdir(parents=True)
    (paths.telemetry_old_dir / "fires.jsonl").write_text('{"hook": "x"}\n')
    conn = telemetry_store.connect(paths.data_home)
    _db.record_migration(
        conn, telemetry_store.LEGACY_JSONL_MIGRATION, applied_at="2026-08-05T00:00:00Z"
    )
    conn.commit()
    conn.close()

    results = {r.name: r for r in check_pending_data_store_migration(paths)}

    assert results["migration-to-1.0.0:telemetry"].status == Status.WARN
    assert "already ran" in results["migration-to-1.0.0:telemetry"].reason


def test_check_pending_migration_warns_when_already_migrated_but_old_files_remain(
    tmp_path: Path,
) -> None:
    paths = _legacy_paths(tmp_path)
    (paths.ccmsg_old_root / "projects" / "alpha" / "inbox").mkdir(parents=True)
    (paths.ccmsg_old_root / "projects" / "alpha" / "inbox" / "msg.md").write_text("x")
    paths.data_home.mkdir(parents=True)
    conn = _db.connect(
        paths.data_home / "ccmsg.db",
        ddl="CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY);",
    )
    conn.execute("INSERT INTO messages (id) VALUES (1)")
    conn.commit()
    conn.close()

    results = {r.name: r for r in check_pending_data_store_migration(paths)}

    assert results["migration-to-1.0.0:ccmsg"].status == Status.WARN
    assert "already ran" in results["migration-to-1.0.0:ccmsg"].reason


def test_run_all_checks_skips_pending_migration_when_paths_none(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"

    results = run_all_checks(
        installed_version="1.0.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
    )

    assert not any(r.name.startswith("migration-to-1.0.0:") for r in results)


def test_run_all_checks_includes_pending_migration_when_paths_given(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"

    results = run_all_checks(
        installed_version="1.0.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
        legacy_migration_paths=_legacy_paths(tmp_path),
    )

    assert any(r.name.startswith("migration-to-1.0.0:") for r in results)


# ---------- check_pending_pdata_migration ----------

def test_check_pending_pdata_migration_ok_when_projects_root_missing(tmp_path: Path) -> None:
    results = check_pending_pdata_migration(tmp_path / "does-not-exist")
    assert len(results) == 1
    assert results[0].status == Status.OK


def test_check_pending_pdata_migration_ok_when_no_projects_have_archive(tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    results = check_pending_pdata_migration(tmp_path)
    assert len(results) == 1
    assert results[0].status == Status.OK


def test_check_pending_pdata_migration_warns_for_undeleted_archive(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    archive_dir = project_dir / ".pdata-migrated"
    archive_dir.mkdir(parents=True)
    (archive_dir / "ideas.csv").write_text("idea\nfirst\n")
    (archive_dir / "MANIFEST.md").write_text("- migrated ideas.csv\n")

    results = check_pending_pdata_migration(tmp_path)

    assert len(results) == 1
    assert results[0].status == Status.WARN
    assert "demo" in results[0].name
    assert "1 archived" in results[0].reason


def test_check_pending_pdata_migration_ignores_manifest_file_itself(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    archive_dir = project_dir / ".pdata-migrated"
    archive_dir.mkdir(parents=True)
    (archive_dir / "MANIFEST.md").write_text("nothing archived yet\n")

    results = check_pending_pdata_migration(tmp_path)
    assert results[0].status == Status.OK


def test_check_pending_pdata_migration_reports_one_result_per_pending_project(
    tmp_path: Path,
) -> None:
    for name in ("demo", "other"):
        archive_dir = tmp_path / name / ".pdata-migrated"
        archive_dir.mkdir(parents=True)
        (archive_dir / "x.csv").write_text("x\n")

    results = check_pending_pdata_migration(tmp_path)
    assert {r.status for r in results} == {Status.WARN}
    assert {r.name for r in results} == {"pdata-init:pending:demo", "pdata-init:pending:other"}


def test_run_all_checks_includes_pdata_migration_check_when_projects_root_given(
    tmp_path: Path,
) -> None:
    results = run_all_checks(
        installed_version="1.2.0",
        settings_path=tmp_path / "settings.json",
        bundle_path=tmp_path / "bundle.json",
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={},
        skip_pypi=True,
        projects_root=tmp_path / "cc",
    )
    assert any(r.name.startswith("pdata-init:pending") for r in results)


def test_run_all_checks_skips_pdata_migration_check_when_projects_root_omitted(
    tmp_path: Path,
) -> None:
    results = run_all_checks(
        installed_version="1.2.0",
        settings_path=tmp_path / "settings.json",
        bundle_path=tmp_path / "bundle.json",
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={},
        skip_pypi=True,
    )
    assert not any(r.name.startswith("pdata-init:pending") for r in results)


# ---------- bundled ccsched job check ----------


def test_check_ccsched_job_registered_ok_when_present_and_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    from cc_session_tools.lib.scheduler import registry
    from cc_session_tools.lib.scheduler.jobspec import validate_job_fields

    spec = validate_job_fields(
        job_id="pm-session-output-reconcile", cadence="every:7d", coalesce="one",
        command=["ccst", "pdata", "reconcile-session-output", "--all-projects"],
        surface=True, enabled=True, catchup_window="7d", timeout="300s",
    )
    registry.add_job(spec)

    result = check_ccsched_job_registered("pm-session-output-reconcile")
    assert result.status == Status.OK


def test_check_ccsched_job_registered_warns_when_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    result = check_ccsched_job_registered("pm-session-output-reconcile")
    assert result.status == Status.WARN
    assert "not registered" in result.reason


def test_check_ccsched_job_registered_warns_when_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    from cc_session_tools.lib.scheduler import registry
    from cc_session_tools.lib.scheduler.jobspec import validate_job_fields

    spec = validate_job_fields(
        job_id="pm-session-output-reconcile", cadence="every:7d", coalesce="one",
        command=["ccst", "pdata", "reconcile-session-output", "--all-projects"],
        surface=True, enabled=True, catchup_window="7d", timeout="300s",
    )
    registry.add_job(spec)
    registry.set_enabled("pm-session-output-reconcile", False)

    result = check_ccsched_job_registered("pm-session-output-reconcile")
    assert result.status == Status.WARN
    assert "disabled" in result.reason


def test_run_all_checks_includes_bundled_ccsched_job_checks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"
    results = run_all_checks(
        installed_version="1.2.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
    )
    names = [r.name for r in results]
    assert "ccsched-job:pm-session-output-reconcile" in names


def test_check_pdata_verify_warns_when_never_run(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import Status, check_pdata_verify

    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    results = check_pdata_verify(["never-verified"])
    assert len(results) == 1
    assert results[0].status == Status.WARN
    assert results[0].name == "pdata-verify:never-verified"
    assert "not run yet" in results[0].reason


def test_check_pdata_verify_ok_when_last_run_clean(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import Status, check_pdata_verify
    from cc_session_tools.lib.pdata import init_paths, service, verify

    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="notes", content="x",
                       file_path=None, fields={}, created_at=1)
    verify.run_verify(project="demo", full=True)

    results = check_pdata_verify(["demo"])
    assert results[0].status == Status.OK


def test_check_pdata_verify_fails_when_last_run_had_a_fail_issue(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import Status, check_pdata_verify
    from cc_session_tools.lib.pdata import init_paths, service, verify

    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    monkeypatch.setenv(init_paths.PROJECTS_ROOT_ENV, str(tmp_path / "projects"))
    service.add_record(project="demo", record_group="filings", content="x",
                       file_path="missing.pdf", fields={}, created_at=1)
    verify.run_verify(project="demo", full=True)

    results = check_pdata_verify(["demo"])
    assert results[0].status == Status.FAIL
    assert "issue(s)" in results[0].reason


def test_check_pdata_verify_returns_one_result_per_project(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import check_pdata_verify

    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    results = check_pdata_verify(["alpha", "beta"])
    assert {r.name for r in results} == {"pdata-verify:alpha", "pdata-verify:beta"}


def test_run_all_checks_includes_pdata_verify_when_projects_given(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import run_all_checks

    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path / "project-db"))
    # Isolates the ungated check_ccsched_job_registered loop run_all_checks runs over every
    # BUNDLED_CCSCHED_JOBS entry — without this, registry.load_registry() resolves
    # scheduler.store.connect() to the real production ccsched.db (CC_SCHEDULER_DIR, else
    # CCST_DATA_HOME/paths.data_home()) and creates its schema there.
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"
    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
        pdata_verify_projects=["demo"],
    )
    assert any(r.name == "pdata-verify:demo" for r in results)


# ---------- check_sessions_project_dir_absolute ----------


def test_check_sessions_project_dir_absolute_warns_on_bad_row(tmp_path):
    from cc_session_tools.lib import sessions_db
    from cc_session_tools.lib.doctor import Status, check_sessions_project_dir_absolute

    db_path = tmp_path / "sessions.db"
    conn = sessions_db.connect(path=db_path)
    conn.execute(
        "INSERT INTO sessions (project_dir, basename, start_date, discovered_at) "
        "VALUES ('.', '20260101-bad', '20260101', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    results = check_sessions_project_dir_absolute(db_path)
    assert len(results) == 1
    assert results[0].status == Status.WARN
    assert "1 " in results[0].reason
    assert "ccst repair sessions" in results[0].reason


def test_check_sessions_project_dir_absolute_ok_when_clean(tmp_path):
    from cc_session_tools.lib import sessions_db
    from cc_session_tools.lib.doctor import Status, check_sessions_project_dir_absolute

    db_path = tmp_path / "sessions.db"
    sessions_db.ensure_session_row(tmp_path / "repos" / "proj", "20260101-good", path=db_path)

    results = check_sessions_project_dir_absolute(db_path)
    assert results[0].status == Status.OK


def test_run_all_checks_includes_sessions_project_dir_check_when_path_given(tmp_path: Path) -> None:
    from cc_session_tools.lib import sessions_db

    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "config" / "hooks-bundle.json"
    db_path = tmp_path / "sessions.db"
    sessions_db.ensure_session_row(tmp_path / "repos" / "proj", "20260101-good", path=db_path)

    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
        sessions_db_path=db_path,
    )
    assert any(r.name == "sessions:project-dir-absolute" for r in results)


def test_run_all_checks_skips_sessions_project_dir_check_when_path_none(tmp_path: Path) -> None:
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
    assert not any(r.name == "sessions:project-dir-absolute" for r in results)


def test_run_all_checks_skips_pdata_verify_when_projects_none(monkeypatch, tmp_path):
    from cc_session_tools.lib.doctor import run_all_checks

    # Same isolation as the test above — run_all_checks() unconditionally exercises the
    # check_ccsched_job_registered loop regardless of pdata_verify_projects.
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    settings = tmp_path / "settings.json"
    settings.write_text('{"hooks": {}}')
    bundle = Path(__file__).parent.parent / "src" / "cc_session_tools" / "config" / "hooks-bundle.json"
    results = run_all_checks(
        installed_version="0.11.0",
        settings_path=settings,
        bundle_path=bundle,
        skills_source_dir=None,
        skills_target_dir=tmp_path / "skills",
        env={"CLAUDE_SESSION_TOOLS_REPO_ROOT": None, "CLAUDE_SESSION_TOOLS_PROJ_ROOT": None},
        skip_pypi=True,
        pdata_verify_projects=None,
    )
    assert not any(r.name.startswith("pdata-verify:") for r in results)
