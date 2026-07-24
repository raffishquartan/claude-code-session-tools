# tests/messaging/test_ccmsg_cli.py  (send portion)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(args: list[str], env_root: Path, extra_env: dict[str, str] | None = None):
    import os

    env = dict(os.environ)
    env["CCST_MESSAGES_ROOT"] = str(env_root)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", *args],
        capture_output=True, text=True, env=env,
    )


def test_send_happy_path(tmp_path: Path) -> None:
    res = _run(
        ["send", "--to-project", "alpha", "--subject", "Hi", "--body", "Body",
         "--from-project", "oneshot", "--from-session", "s", "--from-uuid", "u",
         "--from-partition", "projects/oneshot", "--to-partition", "projects/alpha"],
        tmp_path,
    )
    assert res.returncode == 0, res.stderr
    mid = res.stdout.strip()
    read = _run(["read", mid], tmp_path)
    assert read.returncode == 0
    assert "Hi" in read.stdout


def test_send_rejects_no_recipient(tmp_path: Path) -> None:
    res = _run(["send", "--subject", "Hi", "--body", "B",
                "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
                "--from-partition", "projects/o", "--to-partition", "projects/a"],
               tmp_path)
    assert res.returncode == 2
    assert "exactly one" in (res.stderr + res.stdout).lower()


def test_send_rejects_two_recipients(tmp_path: Path) -> None:
    res = _run(["send", "--to-project", "a", "--to-session", "u2",
                "--subject", "Hi", "--body", "B",
                "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
                "--from-partition", "projects/o", "--to-partition", "projects/a"],
               tmp_path)
    assert res.returncode == 2


def test_send_rejects_empty_body(tmp_path: Path) -> None:
    res = _run(["send", "--to-project", "a", "--subject", "Hi", "--body", "",
                "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
                "--from-partition", "projects/o", "--to-partition", "projects/a"],
               tmp_path)
    assert res.returncode == 2


def test_send_rejects_relative_attachment(tmp_path: Path) -> None:
    res = _run(["send", "--to-project", "a", "--subject", "Hi", "--body", "B",
                "--attach", "relative/path.md",
                "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
                "--from-partition", "projects/o", "--to-partition", "projects/a"],
               tmp_path)
    assert res.returncode == 2
    assert "absolute" in (res.stderr + res.stdout).lower()


def test_send_body_file_happy_path(tmp_path: Path) -> None:
    body_path = tmp_path / "body.md"
    body_path.write_text("From a file.\n", encoding="utf-8")
    res = _run(["send", "--to-project", "alpha", "--subject", "Hi",
                "--body-file", str(body_path),
                "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
                "--from-partition", "projects/o", "--to-partition", "projects/alpha"],
               tmp_path)
    assert res.returncode == 0, res.stderr
    mid = res.stdout.strip()
    read = _run(["read", mid], tmp_path)
    assert read.returncode == 0
    assert "From a file." in read.stdout


def test_send_rejects_unreadable_body_file(tmp_path: Path) -> None:
    res = _run(["send", "--to-project", "alpha", "--subject", "Hi",
                "--body-file", str(tmp_path / "does-not-exist.md"),
                "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
                "--from-partition", "projects/o", "--to-partition", "projects/alpha"],
               tmp_path)
    assert res.returncode == 2
    assert "body file" in (res.stderr + res.stdout).lower()


def test_read_happy_path(tmp_path: Path) -> None:
    send = _run(
        ["send", "--to-project", "alpha", "--subject", "Greetings", "--body", "Hello body",
         "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
         "--from-partition", "projects/o", "--to-partition", "projects/alpha"],
        tmp_path,
    )
    assert send.returncode == 0, send.stderr
    mid = send.stdout.strip()
    res = _run(["read", mid], tmp_path)
    assert res.returncode == 0, res.stderr
    assert "Greetings" in res.stdout
    assert "Hello body" in res.stdout


def test_read_missing_id_errors(tmp_path: Path) -> None:
    res = _run(["read", "does-not-exist"], tmp_path)
    assert res.returncode != 0
    assert "not found" in (res.stderr + res.stdout).lower()


def test_list_empty_store_ok(tmp_path: Path) -> None:
    res = _run(["list"], tmp_path)
    assert res.returncode == 0


def test_claim_missing_id_errors(tmp_path: Path) -> None:
    res = _run(["claim", "nope", "--uuid", "u", "--session", "s"], tmp_path)
    assert res.returncode != 0


def test_archive_missing_id_errors(tmp_path: Path) -> None:
    res = _run(["archive", "nope"], tmp_path)
    assert res.returncode != 0


def test_deliver_stdin_delivers_project_message(tmp_path: Path) -> None:
    proj_root = tmp_path / "proj"
    (proj_root / "alpha").mkdir(parents=True)
    store_dir = tmp_path / "store"
    proj_env = {"CLAUDE_SESSION_TOOLS_PROJ_ROOT": str(proj_root)}
    send = _run(
        ["send", "--to-project", "alpha", "--subject", "Ping", "--body", "hi there",
         "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
         "--from-partition", "projects/o", "--to-partition", "projects/alpha"],
        store_dir, proj_env,
    )
    assert send.returncode == 0, send.stderr
    payload = json.dumps({"session_id": "u1", "cwd": str(proj_root / "alpha")})
    env = dict(os.environ)
    env["CCST_MESSAGES_ROOT"] = str(store_dir)
    env.update(proj_env)
    res = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "deliver", "--stdin", "--mode", "full"],
        input=payload, capture_output=True, text=True, env=env,
    )
    assert res.returncode == 0, res.stderr
    assert "Ping" in res.stdout


def _send_env(store_root: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env["CCST_MESSAGES_ROOT"] = str(store_root)
    env.pop("CLAUDE_SESSION_TOOLS_PROJ_ROOT", None)
    env.pop("CLAUDE_SESSION_TOOLS_REPO_ROOT", None)
    env.update(extra)
    return env


def test_send_derives_uuid_from_env_and_routes_session_to_global(tmp_path: Path) -> None:
    env = _send_env(tmp_path, CLAUDE_CODE_SESSION_ID="env-sender-uuid")
    res = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "send",
         "--to-session", "target-uuid", "--subject", "Hi", "--body", "Body"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert res.returncode == 0, res.stderr

    def _list(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "list", *extra],
            capture_output=True, text=True, env=env, cwd=str(tmp_path),
        )

    # Routed to the _global partition (session-addressed), taken there by uuid.
    glob_rows = _list("--partition", "_global").stdout.strip().splitlines()
    assert len(glob_rows) == 1
    assert "session=target-uuid" in glob_rows[0]
    # from_uuid derived from $CLAUDE_CODE_SESSION_ID.
    assert len(_list("--from-uuid", "env-sender-uuid").stdout.strip().splitlines()) == 1


def test_send_routes_project_to_project_partition(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    (proj / "alpha").mkdir(parents=True)
    store_dir = tmp_path / "store"
    env = _send_env(store_dir, CLAUDE_CODE_SESSION_ID="u",
                    CLAUDE_SESSION_TOOLS_PROJ_ROOT=str(proj))
    res = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "send",
         "--to-project", "alpha", "--subject", "Hi", "--body", "B"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert res.returncode == 0, res.stderr
    listed = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "list",
         "--partition", "projects/alpha"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert len(listed.stdout.strip().splitlines()) == 1


def test_send_errors_without_session_uuid(tmp_path: Path) -> None:
    env = _send_env(tmp_path)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    res = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "send",
         "--to-project", "alpha", "--subject", "Hi", "--body", "B"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert res.returncode == 2
    assert "session uuid" in (res.stderr + res.stdout).lower()
