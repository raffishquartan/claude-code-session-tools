from __future__ import annotations

import json
import os
import shutil
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
    return env


def test_pdata_init_dry_run_empty_project(base_env, tmp_path):
    (tmp_path / "projects" / "biz").mkdir(parents=True)
    r = _run(base_env, "pdata", "init", "--project", "biz")
    assert r.returncode == 0, r.stderr
    assert "no files found" in r.stdout


def test_pdata_init_dry_run_classifies_and_writes_proposal(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "CLAUDE.md").write_text("# demo\n")
    (project_dir / "ideas.csv").write_text("idea,priority\nfirst,1\n")

    r = _run(base_env, "pdata", "init", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "[folder-owned] CLAUDE.md" in r.stdout
    assert "group=ideas strategy=csv-rows" in r.stdout
    assert (project_dir / ".ccst-pdata-proposal.json").exists()


def test_pdata_init_dry_run_prints_doc_update_prompt_path(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\n")

    r = _run(base_env, "pdata", "init", "--project", "demo")
    assert r.returncode == 0, r.stderr
    assert "pdata-migration-claude-md-update.md" in r.stdout
    # Must tell the user to run it in a fresh session started in the project dir - the
    # prompt's own Step 1 aborts otherwise, so a bare path alone invites running it inline
    # in whatever session called `ccst pdata init`.
    assert "new Claude Code session" in r.stdout
    assert str(project_dir) in r.stdout


def test_pdata_init_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "init", "--project", "../escape")
    assert r.returncode == 2
    assert "project" in r.stderr


def test_pdata_init_rejects_malformed_proposal_file(base_env, tmp_path):
    """A hand-edited .ccst-pdata-proposal.json missing a required key must produce
    the documented exit-2 validation error, not an uncaught KeyError traceback."""
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / ".ccst-pdata-proposal.json").write_text(
        json.dumps({"entries": [{"path": "x", "classification": "folder-owned"}]})
    )  # missing the required "project" key

    r = _run(base_env, "pdata", "init", "--project", "demo")
    assert r.returncode == 2
    assert "malformed manifest" in r.stderr


def test_pdata_init_write_end_to_end_imports_and_cuts_over(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\nsecond\n")
    base_env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")

    r_dry = _run(base_env, "pdata", "init", "--project", "demo")
    assert r_dry.returncode == 0, r_dry.stderr

    r_write = _run(base_env, "pdata", "init", "--project", "demo", "--write")
    assert r_write.returncode == 0, r_write.stderr
    assert "Wrote 2 record(s)" in r_write.stdout

    assert not (project_dir / "ideas.csv").exists()
    assert (project_dir / ".pdata-migrated" / "ideas.csv").exists()

    r_list = _run(base_env, "pdata", "list", "--project", "demo", "--group", "ideas",
                  "--format", "json")
    assert r_list.returncode == 0, r_list.stderr
    assert "first" in r_list.stdout and "second" in r_list.stdout


def test_pdata_init_write_without_prior_dry_run_errors(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    r = _run(base_env, "pdata", "init", "--project", "demo", "--write")
    assert r.returncode == 2
    assert "proposal" in r.stderr

    # This is the FileNotFoundError/ValueError branch inside WriteLog's `with` block (project
    # root resolution already succeeded), so it must still end in the ERROR sentinel.
    log_content = (project_dir / "ccst-pdata-init-write.log").read_text()
    assert log_content.rstrip().splitlines()[-1].startswith("ERROR: ")


def test_pdata_init_write_aborts_on_bad_file_path_without_cutover(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text("doc_path,note\n/etc/passwd,bad\n")
    base_env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")

    _run(base_env, "pdata", "init", "--project", "demo")
    proposal_path = project_dir / ".ccst-pdata-proposal.json"
    data = json.loads(proposal_path.read_text())
    data["entries"][0]["file_path_column"] = "doc_path"
    proposal_path.write_text(json.dumps(data))

    r_write = _run(base_env, "pdata", "init", "--project", "demo", "--write")
    assert r_write.returncode == 1
    assert "verification failed" in r_write.stderr.lower()
    assert (project_dir / "docs.csv").exists()
    assert not (project_dir / ".pdata-migrated").exists()


def test_pdata_init_write_streams_progress_and_writes_log_file(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\nsecond\n")
    base_env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")

    _run(base_env, "pdata", "init", "--project", "demo")
    r_write = _run(base_env, "pdata", "init", "--project", "demo", "--write")

    assert r_write.returncode == 0, r_write.stderr
    assert "Importing 1 file(s)" in r_write.stdout
    assert "Backing up" in r_write.stdout
    assert "Cutting over" in r_write.stdout

    log_path = project_dir / "ccst-pdata-init-write.log"
    assert log_path.exists()
    log_content = log_path.read_text()
    assert "Importing 1 file(s)" in log_content
    assert "Wrote 2 record(s)" in log_content


def test_pdata_init_write_log_captures_verification_failure(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text("doc_path,note\n/etc/passwd,bad\n")
    base_env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")

    _run(base_env, "pdata", "init", "--project", "demo")
    proposal_path = project_dir / ".ccst-pdata-proposal.json"
    data = json.loads(proposal_path.read_text())
    data["entries"][0]["file_path_column"] = "doc_path"
    proposal_path.write_text(json.dumps(data))

    r_write = _run(base_env, "pdata", "init", "--project", "demo", "--write")

    assert r_write.returncode == 1
    # Assert both channels independently (not just the tee'd log file), so a regression that
    # broke only the stderr side wouldn't slip past this test.
    assert "verification failed" in r_write.stderr.lower()
    log_content = (project_dir / "ccst-pdata-init-write.log").read_text()
    assert "verification failed" in log_content.lower()


def test_pdata_init_write_success_ends_with_success_sentinel_and_verify_command(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\nsecond\n")
    base_env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")

    _run(base_env, "pdata", "init", "--project", "demo")
    r_write = _run(base_env, "pdata", "init", "--project", "demo", "--write")

    assert r_write.returncode == 0, r_write.stderr
    assert "ccst pdata verify --project demo --full" in r_write.stdout
    assert "pdata-migration-claude-md-update.md" in r_write.stdout
    assert "pdata-migration-skills-update.md" in r_write.stdout
    assert r_write.stdout.count("new Claude Code session") == 2  # one per prompt reminder
    assert str(project_dir) in r_write.stdout
    assert r_write.stdout.rstrip().splitlines()[-1] == "SUCCESS"

    log_content = (project_dir / "ccst-pdata-init-write.log").read_text()
    assert log_content.rstrip().splitlines()[-1] == "SUCCESS"


def test_pdata_init_write_verification_failure_ends_with_error_sentinel(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "docs.csv").write_text("doc_path,note\n/etc/passwd,bad\n")
    base_env["CCST_PDATA_BACKUP_DIR"] = str(tmp_path / "backups")

    _run(base_env, "pdata", "init", "--project", "demo")
    proposal_path = project_dir / ".ccst-pdata-proposal.json"
    data = json.loads(proposal_path.read_text())
    data["entries"][0]["file_path_column"] = "doc_path"
    proposal_path.write_text(json.dumps(data))

    r_write = _run(base_env, "pdata", "init", "--project", "demo", "--write")

    assert r_write.returncode == 1
    assert r_write.stdout.rstrip().splitlines()[-1].startswith("ERROR: ")
    log_content = (project_dir / "ccst-pdata-init-write.log").read_text()
    assert log_content.rstrip().splitlines()[-1].startswith("ERROR: ")


def test_pdata_init_rehearse_does_not_touch_real_project(base_env, tmp_path):
    project_dir = tmp_path / "projects" / "demo"
    project_dir.mkdir(parents=True)
    (project_dir / "ideas.csv").write_text("idea\nfirst\n")
    rehearsal_dir = tmp_path / "rehearsal-demo"
    shutil.copytree(project_dir, rehearsal_dir)
    real_backup_dir = tmp_path / "backups"
    base_env["CCST_PDATA_BACKUP_DIR"] = str(real_backup_dir)

    _run(base_env, "pdata", "init", "--project", "demo", "--rehearse", str(rehearsal_dir))
    r_write = _run(base_env, "pdata", "init", "--project", "demo",
                   "--rehearse", str(rehearsal_dir), "--write")
    assert r_write.returncode == 0, r_write.stderr

    assert (project_dir / "ideas.csv").exists()
    r_list_real = _run(base_env, "pdata", "list", "--project", "demo", "--group", "ideas")
    assert r_list_real.returncode == 0
    assert "first" not in r_list_real.stdout
    # the rehearsal's backup tarball must never land in the real backup dir
    assert not real_backup_dir.exists() or not any(real_backup_dir.iterdir())
