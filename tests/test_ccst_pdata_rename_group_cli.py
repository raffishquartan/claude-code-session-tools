from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import init_paths


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


def _add_record(base_env, group: str) -> None:
    r = _run(base_env, "pdata", "add", "--project", "demo", "--group", group, "--content", "x")
    assert r.returncode == 0, r.stderr


def _schema_list_groups(base_env) -> str:
    r = _run(base_env, "pdata", "schema", "list", "--project", "demo")
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_rename_group_dry_run_reports_plan_without_writing(base_env, tmp_path):
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    _add_record(base_env, "old-name")

    r = _run(base_env, "pdata", "rename-group", "--project", "demo",
              "--from", "old-name", "--to", "new-name")

    assert r.returncode == 0, r.stderr
    assert "old-name -> new-name" in r.stdout
    assert "1 row(s)" in r.stdout

    listed = _schema_list_groups(base_env)
    assert "old-name" in listed
    assert "new-name" not in listed  # dry-run: nothing renamed


def test_rename_group_write_renames_and_reports_backup(base_env, tmp_path):
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    _add_record(base_env, "old-name")

    r = _run(base_env, "pdata", "rename-group", "--project", "demo",
              "--from", "old-name", "--to", "new-name", "--write")

    assert r.returncode == 0, r.stderr
    assert "Renamed record_group 'old-name' -> 'new-name': 1 row(s)" in r.stdout
    assert "Backup:" in r.stdout
    assert "SUCCESS" in r.stdout

    listed = _schema_list_groups(base_env)
    assert "new-name" in listed
    assert "old-name" not in listed


def test_rename_group_rejects_target_that_already_has_rows(base_env, tmp_path):
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    _add_record(base_env, "old-name")
    _add_record(base_env, "new-name")

    r = _run(base_env, "pdata", "rename-group", "--project", "demo",
              "--from", "old-name", "--to", "new-name", "--write")

    assert r.returncode == 2
    assert "already exists" in r.stderr


def test_rename_group_rejects_unknown_source_group(base_env, tmp_path):
    (tmp_path / "projects" / "demo").mkdir(parents=True)

    r = _run(base_env, "pdata", "rename-group", "--project", "demo",
              "--from", "nope", "--to", "new-name")

    assert r.returncode == 2
    assert "no such record_group" in r.stderr


def test_rename_group_write_updates_manifest_entry(base_env, tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    _add_record(base_env, "old-name")
    proposal = {
        "project": "demo",
        "entries": [{
            "path": "receipts.csv", "classification": "db-owned", "reviewed": True,
            "record_group": "old-name", "strategy": "csv-rows",
            "delimiter": None, "content_column": None, "file_path_column": None, "fields": [],
        }],
    }
    (project_root / init_paths.PROPOSAL_FILENAME).write_text(json.dumps(proposal))

    r = _run(base_env, "pdata", "rename-group", "--project", "demo",
              "--from", "old-name", "--to", "new-name", "--write")

    assert r.returncode == 0, r.stderr
    assert "1 manifest entry updated" in r.stdout

    reloaded = json.loads((project_root / init_paths.PROPOSAL_FILENAME).read_text())
    assert reloaded["entries"][0]["record_group"] == "new-name"
