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
    return env


def test_pdata_add_content_only(base_env):
    r = _run(base_env, "pdata", "add", "--project", "testproj",
              "--group", "ccst-ideas", "--content", "an idea")
    assert r.returncode == 0, r.stderr
    assert "1" in r.stdout


def test_pdata_add_rejects_invalid_group(base_env):
    r = _run(base_env, "pdata", "add", "--project", "testproj",
              "--group", "Not Valid", "--content", "an idea")
    assert r.returncode == 2
    assert "record_group" in r.stderr


def test_pdata_add_accepts_created_at_flag(base_env):
    """CLI-level regression test for spec §5's `--created-at <epoch>` flag: confirms argparse
    actually accepts it and forwards it to service.add_record without erroring, not just that
    the flag is reachable via the Python API. (Task 12's `ccst pdata get` later adds an
    end-to-end check that the value is actually persisted.)"""
    r = _run(base_env, "pdata", "add", "--project", "testproj",
              "--group", "ccst-ideas", "--content", "an old idea",
              "--created-at", "1000")
    assert r.returncode == 0, r.stderr
    assert "1" in r.stdout


def test_pdata_schema_add_field(base_env):
    r = _run(
        base_env, "pdata", "schema", "add-field", "--project", "testproj",
        "--group", "key-events", "--field", "sender:TEXT", "--description", "who sent it",
    )
    assert r.returncode == 0, r.stderr


def test_pdata_schema_add_field_rejects_bad_field_spec(base_env):
    r = _run(
        base_env, "pdata", "schema", "add-field", "--project", "testproj",
        "--group", "key-events", "--field", "not-a-valid-spec",
    )
    assert r.returncode == 2


def test_pdata_schema_list_and_show(base_env):
    _run(base_env, "pdata", "add", "--project", "testproj", "--group", "filings",
         "--content", "x")
    r_list = _run(base_env, "pdata", "schema", "list", "--project", "testproj")
    assert r_list.returncode == 0
    assert "filings" in r_list.stdout

    r_show = _run(base_env, "pdata", "schema", "show", "--project", "testproj",
                    "--group", "filings")
    assert r_show.returncode == 0
    assert "content" in r_show.stdout


def test_pdata_schema_list_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "schema", "list", "--project", "../escape")
    assert r.returncode == 2


def test_pdata_add_with_field_routes_to_extension_table(base_env):
    _run(base_env, "pdata", "schema", "add-field", "--project", "testproj",
         "--group", "key-events", "--field", "sender:TEXT")
    r = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
              "--content", "an event", "--field", "sender=alice")
    assert r.returncode == 0, r.stderr


def test_pdata_add_rejects_unregistered_field(base_env):
    r = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
              "--content", "an event", "--field", "nope=x")
    assert r.returncode == 2
    assert "unregistered" in r.stderr
