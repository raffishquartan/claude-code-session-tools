from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent),
        env=env,
    )


@pytest.fixture
def base_env(tmp_path):
    env = os.environ.copy()
    env["CCST_PROJECT_DB_DIR"] = str(tmp_path / "project-db")
    env["CCST_PROJECTS_ROOT"] = str(tmp_path / "projects")
    env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")
    return env


def test_reorganize_dry_run_prints_the_move_plan(base_env, tmp_path):
    corr = tmp_path / "projects" / "demo" / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")

    r = _run(base_env, "pdata", "reorganize", "--project", "demo",
              "--folder", "correspondence", "--strategy", "by-year")

    assert r.returncode == 0, r.stderr
    assert "correspondence/2025.03.14-note.md" in r.stdout
    assert "correspondence/2025/2025.03.14-note.md" in r.stdout
    assert (corr / "2025.03.14-note.md").exists()  # dry-run: nothing moved


def test_reorganize_write_moves_files(base_env, tmp_path):
    corr = tmp_path / "projects" / "demo" / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")

    r = _run(base_env, "pdata", "reorganize", "--project", "demo",
              "--folder", "correspondence", "--strategy", "by-year", "--write")

    assert r.returncode == 0, r.stderr
    assert not (corr / "2025.03.14-note.md").exists()
    assert (corr / "2025" / "2025.03.14-note.md").exists()


def test_reorganize_rejects_unknown_strategy(base_env, tmp_path):
    (tmp_path / "projects" / "demo" / "correspondence").mkdir(parents=True)

    r = _run(base_env, "pdata", "reorganize", "--project", "demo",
              "--folder", "correspondence", "--strategy", "by-topic")

    assert r.returncode == 2
    assert "invalid choice" in r.stderr  # argparse choices=[...] rejects it before reorganize.py sees it


def test_reorganize_rejects_bad_project_name(base_env, tmp_path):
    r = _run(base_env, "pdata", "reorganize", "--project", "../escape",
              "--folder", "correspondence", "--strategy", "by-year")

    assert r.returncode == 2
    assert "project" in r.stderr


def test_reorganize_reports_external_references(base_env, tmp_path):
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    (project_root / "CLAUDE.md").write_text("See correspondence/2025.03.14-note.md.\n")

    r = _run(base_env, "pdata", "reorganize", "--project", "demo",
              "--folder", "correspondence", "--strategy", "by-year")

    assert "CLAUDE.md" in r.stdout
    assert "correspondence/2025.03.14-note.md" in r.stdout


def test_reorganize_prints_matched_record_and_reports_backup_on_write(base_env, tmp_path):
    corr = tmp_path / "projects" / "demo" / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")

    r_write = _run(base_env, "pdata", "reorganize", "--project", "demo",
                    "--folder", "correspondence", "--strategy", "by-year", "--write")

    assert r_write.returncode == 0, r_write.stderr
    assert "Moved 1 file(s)" in r_write.stdout
    assert "Backup:" in r_write.stdout
