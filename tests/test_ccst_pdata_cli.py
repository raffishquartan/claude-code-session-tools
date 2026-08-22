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


def test_pdata_get_shows_flattened_fields(base_env):
    _run(base_env, "pdata", "schema", "add-field", "--project", "testproj",
         "--group", "key-events", "--field", "sender:TEXT")
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
                  "--content", "an event", "--field", "sender=alice")
    record_id = r_add.stdout.strip()
    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert r_get.returncode == 0
    assert "alice" in r_get.stdout


def test_pdata_get_missing_id_errors(base_env):
    r = _run(base_env, "pdata", "get", "--project", "testproj", "--id", "999")
    assert r.returncode == 1


def test_pdata_get_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "get", "--project", "../escape", "--id", "1")
    assert r.returncode == 2


def test_pdata_add_created_at_flag_is_persisted(base_env):
    """End-to-end check (deferred from Task 6, which has no `get` yet) that `ccst pdata add
    --created-at` actually lands the given epoch in storage rather than silently falling back
    to 'now'."""
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "ccst-ideas",
                  "--content", "an old idea", "--created-at", "1000")
    record_id = r_add.stdout.strip()
    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert r_get.returncode == 0
    assert "1000" in r_get.stdout


def test_pdata_list_json_format(base_env):
    _run(base_env, "pdata", "add", "--project", "testproj", "--group", "ccst-ideas",
         "--content", "idea one")
    r = _run(base_env, "pdata", "list", "--project", "testproj", "--group", "ccst-ideas",
              "--format", "json")
    assert r.returncode == 0
    import json
    parsed = json.loads(r.stdout)
    assert parsed[0]["content"] == "idea one"


def test_pdata_query_with_where(base_env):
    _run(base_env, "pdata", "schema", "add-field", "--project", "testproj",
         "--group", "key-events", "--field", "sent_at:INTEGER")
    _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
         "--content", "a", "--field", "sent_at=100")
    _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
         "--content", "b", "--field", "sent_at=200")
    r = _run(base_env, "pdata", "query", "--project", "testproj", "--group", "key-events",
              "--where", "sent_at > 150", "--format", "json")
    assert r.returncode == 0, r.stderr
    import json
    parsed = json.loads(r.stdout)
    assert [row["content"] for row in parsed] == ["b"]


def test_pdata_update_happy_path(base_env):
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "old")
    record_id = r_add.stdout.strip()
    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "1", "--content", "new")
    assert r_update.returncode == 0, r_update.stderr
    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert "new" in r_get.stdout


def test_pdata_update_version_conflict_exits_3(base_env):
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "old")
    record_id = r_add.stdout.strip()
    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "99", "--content", "new")
    assert r_update.returncode == 3
    assert "current" in r_update.stdout.lower() or "current" in r_update.stderr.lower()


def test_pdata_update_without_content_preserves_existing_content(base_env):
    """Regression test: --content is optional (spec §5) — a --file-only update must not require
    resending --content, and must not overwrite content as a side effect."""
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "filings",
                  "--content", "original", "--file", "filings/old.md")
    record_id = r_add.stdout.strip()
    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "1", "--file", "filings/new.md")
    assert r_update.returncode == 0, r_update.stderr
    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert "original" in r_get.stdout
    assert "filings/new.md" in r_get.stdout


def test_pdata_update_without_file_preserves_existing_file_path(base_env):
    """Regression test: a content-only update (--file omitted) must not silently null out a
    previously-set file_path."""
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "filings",
                  "--content", "old", "--file", "filings/keep.md")
    record_id = r_add.stdout.strip()
    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "1", "--content", "new")
    assert r_update.returncode == 0, r_update.stderr
    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert "filings/keep.md" in r_get.stdout


def test_pdata_update_field_null_literal_sets_true_null(base_env):
    """Regression test: --field name=null (the literal token null, unquoted) must set the
    column to real SQL NULL, not the four-character string "null" - check-tesco-shop-due's
    SKILL.md already documents --field next_upcoming_delivery=null as the way to clear that
    field, so the CLI must actually honour it."""
    _run(base_env, "pdata", "schema", "add-field", "--project", "testproj",
         "--group", "key-events", "--field", "sender:TEXT")
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "key-events",
                  "--content", "an event", "--field", "sender=alice")
    record_id = r_add.stdout.strip()

    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "1", "--field", "sender=null")
    assert r_update.returncode == 0, r_update.stderr

    r_list = _run(base_env, "pdata", "list", "--project", "testproj", "--group", "key-events",
                    "--format", "json")
    import json
    parsed = json.loads(r_list.stdout)
    assert parsed[0]["sender"] is None


def test_pdata_update_rejects_empty_update(base_env):
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "x")
    record_id = r_add.stdout.strip()
    r_update = _run(base_env, "pdata", "update", "--project", "testproj", "--id", record_id,
                      "--version", "1")
    assert r_update.returncode == 2
    assert "at least one" in r_update.stderr.lower()


def test_pdata_delete_then_restore(base_env):
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "x")
    record_id = r_add.stdout.strip()

    r_del = _run(base_env, "pdata", "delete", "--project", "testproj", "--id", record_id,
                  "--version", "1")
    assert r_del.returncode == 0, r_del.stderr

    r_get = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert r_get.returncode == 1

    r_restore = _run(base_env, "pdata", "restore", "--project", "testproj", "--id", record_id)
    assert r_restore.returncode == 0, r_restore.stderr

    r_get2 = _run(base_env, "pdata", "get", "--project", "testproj", "--id", record_id)
    assert r_get2.returncode == 0


def test_pdata_delete_version_conflict_exits_3(base_env):
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "x")
    record_id = r_add.stdout.strip()
    r_del = _run(base_env, "pdata", "delete", "--project", "testproj", "--id", record_id,
                  "--version", "99")
    assert r_del.returncode == 3


def test_pdata_delete_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "delete", "--project", "../escape", "--id", "1",
              "--version", "1")
    assert r.returncode == 2


def test_pdata_restore_rejects_bad_project_name(base_env):
    r = _run(base_env, "pdata", "restore", "--project", "../escape", "--id", "1")
    assert r.returncode == 2


def test_pdata_query_include_deleted(base_env):
    """Moved here from Task 14: depends on `pdata delete`, added in this task."""
    r_add = _run(base_env, "pdata", "add", "--project", "testproj", "--group", "notes",
                  "--content", "gone")
    record_id = r_add.stdout.strip()
    _run(base_env, "pdata", "delete", "--project", "testproj", "--id", record_id,
         "--version", "1")

    import json
    r_default = _run(base_env, "pdata", "query", "--project", "testproj", "--group", "notes",
                       "--where", "content = gone", "--format", "json")
    assert json.loads(r_default.stdout) == []

    r_included = _run(base_env, "pdata", "query", "--project", "testproj", "--group", "notes",
                        "--where", "content = gone", "--include-deleted", "--format", "json")
    assert [row["content"] for row in json.loads(r_included.stdout)] == ["gone"]
