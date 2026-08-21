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


def test_reorganize_rejects_empty_folder(base_env, tmp_path):
    """Regression test: argparse's required=True does not reject an empty string, only a
    missing flag - the empty-string rejection has to happen in reorganize.py itself, and the
    CLI must surface it as the standard exit-2 error, not a raw traceback."""
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    r = _run(base_env, "pdata", "reorganize", "--project", "demo",
              "--folder", "", "--strategy", "by-year")

    assert r.returncode == 2
    assert "empty" in r.stderr


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

    assert r.returncode == 0, r.stderr
    assert "CLAUDE.md" in r.stdout
    assert "correspondence/2025.03.14-note.md" in r.stdout


def test_reorganize_write_reports_file_count_and_backup_path(base_env, tmp_path):
    corr = tmp_path / "projects" / "demo" / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")

    r_write = _run(base_env, "pdata", "reorganize", "--project", "demo",
                    "--folder", "correspondence", "--strategy", "by-year", "--write")

    assert r_write.returncode == 0, r_write.stderr
    assert "Moved 1 file(s)" in r_write.stdout
    assert "Backup:" in r_write.stdout


def test_reorganize_dry_run_prints_matched_pdata_record(base_env, tmp_path):
    """The dry-run branch is the only place a matched pdata record is ever printed - --write's
    success output only reports the move count and backup path, not a per-record breakdown."""
    corr = tmp_path / "projects" / "demo" / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    r_add = _run(base_env, "pdata", "add", "--project", "demo", "--group", "letters",
                  "--content", "x", "--file", "correspondence/2025.03.14-note.md")
    assert r_add.returncode == 0, r_add.stderr

    r = _run(base_env, "pdata", "reorganize", "--project", "demo",
              "--folder", "correspondence", "--strategy", "by-year")

    assert r.returncode == 0, r.stderr
    assert "pdata record" in r.stdout
    assert "group=letters" in r.stdout
    assert "correspondence/2025/2025.03.14-note.md" in r.stdout


def test_reorganize_write_reports_failure_and_rolls_back(base_env, tmp_path):
    """A real, portable failure trigger, avoiding any monkeypatching since this is a
    subprocess-based CLI test: pre-create the exact destination path as a directory instead
    of the file it needs to be. Path.rename() (or `git mv`) raises IsADirectoryError (an
    OSError subclass) trying to rename a file onto an existing directory - note this can't be
    a file directly under correspondence/ itself (that would just get picked up as one of the
    entries to move and moved out of the way first), it has to already exist at the nested
    destination."""
    corr = tmp_path / "projects" / "demo" / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")
    (corr / "2025" / "2025.03.14-note.md").mkdir(parents=True)

    r_write = _run(base_env, "pdata", "reorganize", "--project", "demo",
                    "--folder", "correspondence", "--strategy", "by-year", "--write")

    assert r_write.returncode == 1
    assert "failed, rolled back" in r_write.stderr
    # Nothing actually moved - the original file survives at its flat location.
    assert (corr / "2025.03.14-note.md").exists()


def test_reorganize_write_streams_progress_and_writes_its_own_log_file(base_env, tmp_path):
    """Mirrors test_pdata_init_write_streams_progress_and_writes_log_file (same contract, own
    log filename - ccst-pdata-reorganize-write.log, not ccst-pdata-init-write.log, so the two
    operations never truncate each other's log)."""
    project_root = tmp_path / "projects" / "demo"
    corr = project_root / "correspondence"
    corr.mkdir(parents=True)
    (corr / "2025.03.14-note.md").write_text("x")

    r_write = _run(base_env, "pdata", "reorganize", "--project", "demo",
                    "--folder", "correspondence", "--strategy", "by-year", "--write")

    assert r_write.returncode == 0, r_write.stderr
    assert r_write.stdout.rstrip().splitlines()[-1] == "SUCCESS"

    log_path = project_root / "ccst-pdata-reorganize-write.log"
    assert log_path.exists()
    log_content = log_path.read_text()
    assert "Moved 1 file(s)" in log_content
    assert log_content.rstrip().splitlines()[-1] == "SUCCESS"
