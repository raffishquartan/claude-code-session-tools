"""CLI surface of UUID record ids: argument validation, json output, ordering, help text."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import ids

_ABSENT_ID = "40090682-d73d-4958-a608-000000000000"


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True, cwd=str(Path(__file__).parent), env=env,
    )


@pytest.fixture
def base_env(tmp_path):
    env = os.environ.copy()
    env["CCST_PROJECT_DB_DIR"] = str(tmp_path / "project-db")
    return env


def _add(env: dict[str, str], content: str, *extra: str) -> str:
    r = _run(env, "pdata", "add", "--project", "p", "--group", "notes", "--content", content,
             *extra)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_add_prints_a_canonical_uuid_and_get_round_trips_it(base_env):
    record_id = _add(base_env, "hello")
    assert ids.is_record_id(record_id)
    r = _run(base_env, "pdata", "get", "--project", "p", "--id", record_id)
    assert r.returncode == 0, r.stderr
    assert record_id in r.stdout
    assert "hello" in r.stdout


def test_two_adds_never_print_the_same_id(base_env):
    assert _add(base_env, "a") != _add(base_env, "b")


@pytest.mark.parametrize("verb,extra", [
    ("get", ()),
    ("update", ("--version", "1", "--content", "x")),
    ("delete", ("--version", "1")),
    ("restore", ()),
])
def test_an_integer_id_is_a_usage_error_naming_the_value(base_env, tmp_path, verb, extra):
    r = _run(base_env, "pdata", verb, "--project", "p", "--id", "12", *extra)
    assert r.returncode == 2
    assert "'12' is not a UUID" in r.stderr
    assert not (tmp_path / "project-db").exists()


def test_a_valid_but_absent_uuid_is_record_not_found(base_env):
    _add(base_env, "exists")
    r = _run(base_env, "pdata", "get", "--project", "p", "--id", _ABSENT_ID)
    assert r.returncode == 1
    assert f"record not found: {_ABSENT_ID}" in r.stderr


def test_resolve_choice_with_an_integer_id_is_a_usage_error(base_env):
    r = _run(base_env, "pdata", "resolve", "--project", "p", "--choice", "12=local")
    assert r.returncode == 2
    assert "'12' is not a UUID" in r.stderr


def test_resolve_choice_without_a_choice_is_still_malformed(base_env):
    r = _run(base_env, "pdata", "resolve", "--project", "p", "--choice", _ABSENT_ID)
    assert r.returncode == 2
    assert "malformed --choice (want UUID=local|dump)" in r.stderr


def test_json_output_emits_string_ids(base_env):
    record_id = _add(base_env, "x")
    r = _run(base_env, "pdata", "list", "--project", "p", "--group", "notes", "--format", "json")
    assert r.returncode == 0, r.stderr
    (row,) = json.loads(r.stdout)
    assert row["id"] == record_id
    assert isinstance(row["id"], str)


def test_listing_order_follows_created_at_not_id(base_env):
    # Three records created at t1 < t2 < t3, added in reverse order so insertion order and any
    # id order cannot coincidentally match creation order.
    by_time = {}
    for created_at in ("3000", "1000", "2000"):
        by_time[created_at] = _add(base_env, f"t{created_at}", "--created-at", created_at)
    for verb, extra in (("list", ()), ("query", ("--where", "content LIKE t%"))):
        r = _run(base_env, "pdata", verb, "--project", "p", "--group", "notes",
                 "--format", "json", *extra)
        assert r.returncode == 0, r.stderr
        assert [row["id"] for row in json.loads(r.stdout)] == [
            by_time["1000"], by_time["2000"], by_time["3000"],
        ]


def test_help_names_uuid_arguments(base_env):
    r = _run(base_env, "pdata", "resolve", "--help")
    assert r.returncode == 0
    assert "UUID=local|dump" in r.stdout
    assert "ID=local|dump" not in r.stdout.replace("UUID=local|dump", "")
    for verb in ("get", "update", "delete", "restore"):
        r = _run(base_env, "pdata", verb, "--help")
        assert "--id UUID" in r.stdout
