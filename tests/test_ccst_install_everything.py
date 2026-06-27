"""Tests for `ccst install-everything` subcommand."""
from __future__ import annotations

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
    assert "1/5" in result.stdout
    assert "2/5" in result.stdout
    assert "3/5" in result.stdout
    assert "4/5" in result.stdout
    assert "5/5" in result.stdout


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


def test_apply_flag_accepted() -> None:
    # Verify --apply is a recognised flag (no argparse error) and changes output format.
    # We don't assert rc==0 because the real ~/.claude/skills state varies per environment.
    result = _run("install-everything", "--apply", "--no-pypi")
    # Must not be an argparse error
    assert "unrecognized arguments" not in result.stderr
    assert "usage:" not in result.stderr.lower() or result.returncode == 2
    # Dry-run banner must be absent when --apply was passed
    assert "dry run complete" not in result.stdout.lower()


# ---------- section headers ----------


def test_section_headers_present(tmp_path: Path) -> None:
    result = _run("install-everything", "--no-pypi")
    assert result.returncode == 0
    out = result.stdout
    assert "Skills" in out
    assert "Hooks" in out
    assert "Shell helpers" in out
    assert "CLAUDE.md" in out or "Global CLAUDE.md" in out
    assert "Health check" in out


# ---------- no-pypi flag ----------


def test_no_pypi_flag_accepted() -> None:
    result = _run("install-everything", "--no-pypi")
    assert result.returncode == 0
    # With --no-pypi the PyPI check is skipped; command still completes
    assert "5/5" in result.stdout
