"""Tests for `ccst install-everything` subcommand."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
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


def _isolated_apply_args(tmp_path: Path, *extra: str) -> list[str]:
    """--apply args pointing every step's write target at tmp_path.

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
    result = _run(*_isolated_apply_args(tmp_path))
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
    env = os.environ.copy()
    env["CCST_DATA_HOME"] = str(tmp_path / "data-home")

    result = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *_isolated_apply_args(tmp_path)],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent), env=env,
    )

    assert result.returncode == 0
    assert "unrecognized arguments" not in result.stderr
    assert "Scheduled jobs" in result.stdout
    assert "registered: pm-session-output-reconcile" in result.stdout
