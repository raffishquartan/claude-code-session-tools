"""Tests for `ccst install-everything` subcommand."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pytest_mock import MockerFixture


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
        env=env,
    )


def _make_skills_source(base: Path, names: list[str]) -> Path:
    src = base / "skills"
    src.mkdir(parents=True, exist_ok=True)
    for name in names:
        d = src / name
        d.mkdir()
        (d / "SKILL.md").write_text(f"# {name}\n")
    return src


# ---------- help and basic interface ----------


def test_help_flag() -> None:
    result = _run("install-everything", "--help")
    assert result.returncode == 0
    assert "--apply" in result.stdout
    assert "--no-pypi" in result.stdout


# ---------- dry run ----------


def test_dry_run_is_default(tmp_path: Path) -> None:
    skills_src = _make_skills_source(tmp_path / "src", ["alpha"])
    skills_tgt = tmp_path / "target_skills"
    skills_tgt.mkdir()
    hooks_tgt = tmp_path / "settings.json"
    hooks_tgt.write_text("{}")
    rc_file = tmp_path / ".bashrc"
    rc_file.write_text("")
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("")

    result = _run(
        "install-everything",
        "--no-pypi",
    )

    assert result.returncode == 0
    # Dry run banner must appear
    assert "dry run" in result.stdout.lower()
    # Section headers must be present
    assert "1/6" in result.stdout
    assert "2/6" in result.stdout
    assert "3/6" in result.stdout
    assert "4/6" in result.stdout
    assert "5/6" in result.stdout
    assert "6/6" in result.stdout


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    skills_tgt = tmp_path / "target_skills"
    skills_tgt.mkdir()
    hooks_tgt = tmp_path / "settings.json"
    hooks_tgt.write_text("{}")

    # Capture that no new symlinks were created in default skill target when dry run
    result = _run("install-everything", "--no-pypi")
    assert result.returncode == 0
    # Doctor runs but nothing was written by other steps that wasn't already there
    assert "dry run" in result.stdout.lower()


# ---------- apply mode ----------


def _isolated_apply_env(tmp_path: Path) -> dict[str, str]:
    """The other half of _isolated_apply_args, and NOT optional.

    Step 5 of the six (Scheduled jobs) has no --target flag to point at
    tmp_path: `ccst ccsched-jobs install --apply` writes into the ccsched
    registry, whose location comes from CC_SCHEDULER_DIR / CCST_DATA_HOME,
    not argv. An --apply test that passes _isolated_apply_args but keeps the
    ambient environment therefore registers every BUNDLED_CCSCHED_JOBS entry
    into the developer's real ~/.local/share/claude/ccsched.db - live,
    enabled, and picked up by the next real session-start sweep. That was a
    real incident: it silently provisioned a brand-new bundled job on the
    developer's machine on every `pytest` run while that job was still being
    written. Every --apply call site must pass this env.
    """
    env = os.environ.copy()
    env["CCST_DATA_HOME"] = str(tmp_path / "data-home")
    return env


def _isolated_apply_args(tmp_path: Path, *extra: str) -> list[str]:
    """--apply args pointing every *flag-addressable* step's write target at
    tmp_path. Always pair with _isolated_apply_env() - see its docstring for
    the step this cannot cover.

    install-everything defaults every step's target to the real machine
    (~/.claude/skills, ~/.claude/settings.json, ~/.shellrc.d,
    ~/.claude/CLAUDE.md) when not overridden — tests must never exercise
    --apply without these, or they mutate the developer's actual machine.
    """
    return [
        "install-everything",
        "--apply",
        "--no-pypi",
        "--skills-target", str(tmp_path / "skills"),
        "--hooks-target", str(tmp_path / "settings.json"),
        "--fragments-dir", str(tmp_path / "shellrc.d"),
        "--claude-md-target", str(tmp_path / "CLAUDE.md"),
        *extra,
    ]


def test_apply_flag_accepted(tmp_path: Path) -> None:
    # Verify --apply is a recognised flag (no argparse error) and changes output format.
    result = _run(*_isolated_apply_args(tmp_path), env=_isolated_apply_env(tmp_path))
    assert result.returncode == 0
    # Must not be an argparse error
    assert "unrecognized arguments" not in result.stderr
    # Dry-run banner must be absent when --apply was passed
    assert "dry run complete" not in result.stdout.lower()
    # Confirm writes actually landed in the isolated targets, not the real machine
    assert (tmp_path / "shellrc.d" / "ccl.sh").exists()
    assert (tmp_path / "settings.json").exists()
    assert (tmp_path / "CLAUDE.md").exists()


# ---------- section headers ----------


def test_section_headers_present(tmp_path: Path) -> None:
    result = _run("install-everything", "--no-pypi")
    assert result.returncode == 0
    out = result.stdout
    assert "Skills" in out
    assert "Hooks" in out
    assert "Shell helpers" in out
    assert "CLAUDE.md" in out or "Global CLAUDE.md" in out
    assert "Scheduled jobs" in out
    assert "Health check" in out


# ---------- no-pypi flag ----------


def test_no_pypi_flag_accepted() -> None:
    result = _run("install-everything", "--no-pypi")
    assert result.returncode == 0
    # With --no-pypi the PyPI check is skipped; command still completes
    assert "6/6" in result.stdout


# ---------- bundled ccsched jobs ----------


def test_install_everything_registers_bundled_ccsched_jobs(tmp_path: Path) -> None:
    env = _isolated_apply_env(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *_isolated_apply_args(tmp_path)],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent), env=env,
    )

    assert result.returncode == 0
    assert "unrecognized arguments" not in result.stderr
    assert "Scheduled jobs" in result.stdout
    assert "registered: pm-session-output-reconcile" in result.stdout


# ---------- install-sync marker ----------


def test_apply_records_synced_version(tmp_path: Path) -> None:
    from cc_session_tools import __version__ as version
    from cc_session_tools.lib import install_sync

    env = _isolated_apply_env(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *_isolated_apply_args(tmp_path)],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent), env=env,
    )

    assert result.returncode == 0
    assert install_sync.get_synced_version(
        path=tmp_path / "data-home" / "sessions.db"
    ) == version


def test_dry_run_does_not_record_synced_version(tmp_path: Path) -> None:
    from cc_session_tools.lib import install_sync

    env = _isolated_apply_env(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", "install-everything", "--no-pypi"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent), env=env,
    )

    assert result.returncode == 0
    assert install_sync.get_synced_version(path=tmp_path / "data-home" / "sessions.db") is None


def test_apply_survives_a_write_error_recording_the_sync_marker(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found during final review: a corrupt sessions.db previously crashed
    the whole --apply run at the record_synced() call, after the five
    install steps had already succeeded - discarding their printed summary
    and, worse, making install-everything itself (the exact command the
    interactive gate tells a user to run) unusable on the one machine state
    that most needs it. The five steps' own success (and exit code) must
    survive a failure recording the marker; only a warning should be
    printed.

    Uses a mocked record_synced() rather than an actually-corrupt
    sessions.db so this test isolates exactly the property this specific
    commit owns (the write path degrading gracefully), independent of the
    doctor.py read-side fixes tested separately below and in
    test_ccst_doctor.py. See test_apply_survives_a_corrupt_sessions_db_end_to_end
    below for the real, un-mocked, full-corruption scenario - this one is
    the narrower in-process check (this file's usual subprocess convention
    can't express a mock across the process boundary)."""
    import sqlite3

    from cc_session_tools.cli import ccst as ccst_module
    from cc_session_tools.lib import install_sync

    # Isolate the trailing health check too - it must run against a fresh,
    # valid store, not the real ~/.local/share/claude/. Same CCST_DATA_HOME target
    # _isolated_apply_env() sets for this file's subprocess-based --apply tests, applied via
    # monkeypatch.setenv rather than that helper's env dict, since this call is in-process
    # (_cmd_install_everything called directly, no subprocess) - equivalent isolation, different
    # mechanism because the call itself is different, not a call site that skipped isolation.
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path / "data-home"))
    mocker.patch.object(
        install_sync, "record_synced",
        side_effect=sqlite3.DatabaseError("file is not a database"),
    )
    parser = ccst_module._build_parser()
    args = parser.parse_args(_isolated_apply_args(tmp_path))

    rc = ccst_module._cmd_install_everything(args)

    assert rc == 0


def test_apply_records_synced_version_end_to_end_via_subprocess(tmp_path: Path) -> None:
    """A real, uncorrupted sessions.db must still work exactly as before -
    this is the same scenario as test_apply_records_synced_version above,
    kept as a second, full-subprocess confirmation specifically alongside
    the in-process mock test, so a change that only satisfies the mock
    (e.g. swallowing every exception unconditionally) can't pass silently
    without ever exercising the real write path end-to-end."""
    from cc_session_tools import __version__ as version
    from cc_session_tools.lib import install_sync

    env = _isolated_apply_env(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *_isolated_apply_args(tmp_path)],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent), env=env,
    )

    assert result.returncode == 0
    assert "warning: could not record the install-everything sync marker" not in result.stderr
    assert install_sync.get_synced_version(
        path=tmp_path / "data-home" / "sessions.db"
    ) == version


def test_apply_survives_a_corrupt_sessions_db_end_to_end(tmp_path: Path) -> None:
    """Real, un-mocked reproduction of the full corrupt-sessions.db scenario,
    found incomplete during a second final-review pass: fixing
    record_synced() alone (see the mocked test above) was not sufficient -
    install-everything's own trailing health check crashed on the identical
    corrupt file via a completely different read site
    (check_sessions_project_dir_absolute -> sessions_repair.find_non_absolute_rows
    -> sessions_db.list_sessions), because sqlite3.connect() opens lazily and
    only fails once a query actually touches the file. That site is now
    fixed to FAIL cleanly instead of raising (see doctor.py). This test
    exercises the real end-to-end command, not a mock, specifically to catch
    any FUTURE read site that reintroduces the same gap."""
    data_home = tmp_path / "data-home"
    data_home.mkdir()
    (data_home / "sessions.db").write_bytes(b"not a sqlite database file")

    env = os.environ.copy()
    env["CCST_DATA_HOME"] = str(data_home)

    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *_isolated_apply_args(tmp_path)],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent), env=env,
    )

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert "warning: could not record the install-everything sync marker" in result.stderr


# ---------- run_install_everything core ----------


def test_health_check_false_does_not_run_doctor(tmp_path: Path, mocker: MockerFixture) -> None:
    """Guards against auto-apply silently acquiring doctor's ~1.5 s PyPI
    dependency. The five install steps are 16.8 ms; the trailing health check
    is 1.55 s of it, dominated by a network call. Auto-apply is not a
    diagnostic run and must not pay that on someone else's command."""
    import io

    from cc_session_tools.cli import ccst

    doctor = mocker.patch.object(ccst, "_cmd_doctor", return_value=0)
    buf = io.StringIO()

    rc = ccst.run_install_everything(
        apply=False,
        stream=buf,
        health_check=False,
        skills_target=str(tmp_path / "skills"),
        hooks_target=str(tmp_path / "settings.json"),
        fragments_dir=str(tmp_path / "shellrc.d"),
        claude_md_target=str(tmp_path / "CLAUDE.md"),
    )

    assert rc == 0
    doctor.assert_not_called()


def test_health_check_true_runs_doctor(tmp_path: Path, mocker: MockerFixture) -> None:
    """The _cmd_install_everything adapter path keeps the health check."""
    import io

    from cc_session_tools.cli import ccst

    doctor = mocker.patch.object(ccst, "_cmd_doctor", return_value=0)
    buf = io.StringIO()

    ccst.run_install_everything(
        apply=False,
        stream=buf,
        health_check=True,
        no_pypi=True,
        skills_target=str(tmp_path / "skills"),
        hooks_target=str(tmp_path / "settings.json"),
        fragments_dir=str(tmp_path / "shellrc.d"),
        claude_md_target=str(tmp_path / "CLAUDE.md"),
    )

    doctor.assert_called_once()


def test_step_output_goes_to_the_stream_not_stdout(
    tmp_path: Path, mocker: MockerFixture, capsys
) -> None:
    """Section 4's hard requirement: `ccst sessions list --json` emits
    machine-readable stdout, and scheduler/worker.py carries a job's stdout
    into the ledger as its recorded findings. Interleaving install chatter
    there is a correctness bug, not a cosmetic one."""
    import io

    from cc_session_tools.cli import ccst

    mocker.patch.object(ccst, "_cmd_doctor", return_value=0)
    buf = io.StringIO()

    ccst.run_install_everything(
        apply=False,
        stream=buf,
        health_check=False,
        skills_target=str(tmp_path / "skills"),
        hooks_target=str(tmp_path / "settings.json"),
        fragments_dir=str(tmp_path / "shellrc.d"),
        claude_md_target=str(tmp_path / "CLAUDE.md"),
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Skills" in buf.getvalue()
    assert "Scheduled jobs" in buf.getvalue()
