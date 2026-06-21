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
    assert (tmp_path / "projects" / "alpha" / "inbox").is_dir()


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
    assert (tmp_path / "projects" / "alpha" / "inbox").is_dir()


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
