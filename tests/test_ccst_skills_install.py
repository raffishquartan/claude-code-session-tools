"""Tests for `ccst skills install` subcommand.

All tests use tmp_path for both source and target — never touches real ~/.claude/skills/.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


# ---------- helpers ----------


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


def _make_skills_source(base: Path, names: list[str]) -> Path:
    """Create a fake skills/ source directory with named skill subdirs each containing SKILL.md."""
    src = base / "skills"
    src.mkdir(parents=True, exist_ok=True)
    for name in names:
        skill_dir = src / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"# {name} skill\n")
    return src


def _make_target_dir(base: Path) -> Path:
    tgt = base / "target_skills"
    tgt.mkdir(parents=True, exist_ok=True)
    return tgt


# ---------- Test 1: dry run lists three skills with action 'create' when target is empty ----------



def test_dry_run_lists_create_for_all_skills(tmp_path: Path) -> None:
    src = _make_skills_source(tmp_path / "src", ["alpha", "beta", "gamma"])
    tgt = _make_target_dir(tmp_path / "tgt")

    result = _run("skills", "install", "--source", str(src), "--target", str(tgt))

    assert result.returncode == 0
    assert "alpha" in result.stdout
    assert "beta" in result.stdout
    assert "gamma" in result.stdout
    assert "create" in result.stdout.lower()
    # dry run must NOT create symlinks
    assert not (tgt / "alpha").exists()
    assert not (tgt / "beta").exists()
    assert not (tgt / "gamma").exists()


# ---------- Test 2: --apply creates three symlinks pointing at source paths ----------


def test_apply_creates_symlinks(tmp_path: Path) -> None:
    src = _make_skills_source(tmp_path / "src", ["alpha", "beta", "gamma"])
    tgt = _make_target_dir(tmp_path / "tgt")

    result = _run("skills", "install", "--source", str(src), "--target", str(tgt), "--apply")

    assert result.returncode == 0
    for name in ["alpha", "beta", "gamma"]:
        link = tgt / name
        assert link.is_symlink(), f"{name} should be a symlink"
        assert link.resolve() == (src / name).resolve(), f"{name} symlink target mismatch"


# ---------- Test 3: re-running --apply is idempotent (already-correct) ----------


def test_apply_is_idempotent(tmp_path: Path) -> None:
    src = _make_skills_source(tmp_path / "src", ["alpha", "beta", "gamma"])
    tgt = _make_target_dir(tmp_path / "tgt")

    # First install
    _run("skills", "install", "--source", str(src), "--target", str(tgt), "--apply")

    # Second install — must be no-op and report already-correct
    result = _run("skills", "install", "--source", str(src), "--target", str(tgt), "--apply")

    assert result.returncode == 0
    assert "already-correct" in result.stdout.lower()
    # Symlinks should still be correct
    for name in ["alpha", "beta", "gamma"]:
        link = tgt / name
        assert link.is_symlink()
        assert link.resolve() == (src / name).resolve()


# ---------- Test 4: stale symlink -> wrong-target in dry run; --apply --force fixes it ----------


def test_stale_symlink_wrong_target_and_force(tmp_path: Path) -> None:
    src = _make_skills_source(tmp_path / "src", ["alpha"])
    tgt = _make_target_dir(tmp_path / "tgt")

    # Create a stale symlink pointing to /tmp/somewhere-else
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    stale_link = tgt / "alpha"
    stale_link.symlink_to(elsewhere)

    # Dry run must report wrong-target
    result = _run("skills", "install", "--source", str(src), "--target", str(tgt))
    assert result.returncode == 0
    assert "wrong-target" in result.stdout.lower()
    # still pointing wrong after dry run
    assert stale_link.resolve() == elsewhere.resolve()

    # --apply without --force must refuse
    result = _run("skills", "install", "--source", str(src), "--target", str(tgt), "--apply")
    assert result.returncode != 0
    assert stale_link.resolve() == elsewhere.resolve()

    # --apply --force must fix it
    result = _run(
        "skills", "install", "--source", str(src), "--target", str(tgt), "--apply", "--force"
    )
    assert result.returncode == 0
    assert stale_link.is_symlink()
    assert stale_link.resolve() == (src / "alpha").resolve()


# ---------- Test 5: non-symlink file -> non-symlink-exists in dry run; refuse without force; backup+link with force ----------


def test_non_symlink_file_at_target(tmp_path: Path) -> None:
    src = _make_skills_source(tmp_path / "src", ["alpha"])
    tgt = _make_target_dir(tmp_path / "tgt")

    # Create a real file (not a symlink) at the target location
    real_file = tgt / "alpha"
    real_file.write_text("I am a real file\n")

    # Dry run must report non-symlink-exists
    result = _run("skills", "install", "--source", str(src), "--target", str(tgt))
    assert result.returncode == 0
    assert "non-symlink-exists" in result.stdout.lower()
    assert real_file.is_file() and not real_file.is_symlink()

    # --apply without --force must exit 1
    result = _run("skills", "install", "--source", str(src), "--target", str(tgt), "--apply")
    assert result.returncode == 1
    assert real_file.is_file() and not real_file.is_symlink()

    # --apply --force must move the file aside and create the symlink
    result = _run(
        "skills", "install", "--source", str(src), "--target", str(tgt), "--apply", "--force"
    )
    assert result.returncode == 0
    assert real_file.is_symlink()
    assert real_file.resolve() == (src / "alpha").resolve()
    # The backup must exist somewhere in tgt
    backups = list(tgt.glob("alpha.bak-*"))
    assert len(backups) == 1, "expected exactly one backup file"
    assert backups[0].read_text() == "I am a real file\n"


# ---------- Test 6: --source and --target overrides work ----------


def test_source_and_target_overrides(tmp_path: Path) -> None:
    # Use completely custom source and target paths
    custom_src = _make_skills_source(tmp_path / "custom_src", ["delta", "epsilon"])
    custom_tgt = tmp_path / "custom_tgt"  # intentionally not pre-created; install creates it

    result = _run(
        "skills", "install",
        "--source", str(custom_src),
        "--target", str(custom_tgt),
        "--apply",
    )

    assert result.returncode == 0
    # Target dir must have been created
    assert custom_tgt.is_dir()
    for name in ["delta", "epsilon"]:
        link = custom_tgt / name
        assert link.is_symlink()
        assert link.resolve() == (custom_src / name).resolve()


# ---------- Test 7: directories without SKILL.md are ignored ----------


def test_directory_without_skill_md_is_ignored(tmp_path: Path) -> None:
    src = _make_skills_source(tmp_path / "src", ["valid-skill"])
    # Add a subdirectory without SKILL.md — should be silently skipped
    (src / "not-a-skill").mkdir()
    (src / "not-a-skill" / "README.md").write_text("this is not a skill\n")

    tgt = _make_target_dir(tmp_path / "tgt")

    result = _run("skills", "install", "--source", str(src), "--target", str(tgt))

    assert result.returncode == 0
    assert "valid-skill" in result.stdout
    assert "not-a-skill" not in result.stdout
