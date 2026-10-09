# tests/messaging/test_ccmsg_cli.py  (send portion)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


TARGET_UUID = "0c1d2e3f-1111-4222-8333-666666666666"


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
    res = _run(["send", "--to-project", "a", "--to-session", "11111111-2222-4333-8444-555555555555",
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


def test_read_prints_sent_at_in_human_form_and_stored_value_stays_machine_form(
    tmp_path: Path,
) -> None:
    import re

    send = _run(
        ["send", "--to-project", "alpha", "--subject", "S", "--body", "B",
         "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
         "--from-partition", "projects/o", "--to-partition", "projects/alpha"],
        tmp_path,
    )
    mid = send.stdout.strip()
    res = _run(["read", mid], tmp_path)
    line = next(ln for ln in res.stdout.splitlines() if ln.startswith("sent_at:"))
    assert re.fullmatch(r"sent_at:  \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", line), line
    listing = _run(["list"], tmp_path)
    assert listing.returncode == 0, listing.stderr
    assert "just now" in listing.stdout  # relative_age still parses the stored T...Z value


def test_read_missing_id_errors(tmp_path: Path) -> None:
    res = _run(["read", "does-not-exist"], tmp_path)
    assert res.returncode != 0
    assert "not found" in (res.stderr + res.stdout).lower()


def test_list_empty_store_ok(tmp_path: Path) -> None:
    res = _run(["list"], tmp_path)
    assert res.returncode == 0


def test_list_shows_message_age(tmp_path: Path) -> None:
    proj_root = tmp_path / "proj"
    (proj_root / "alpha").mkdir(parents=True)
    proj_env = {"CLAUDE_SESSION_TOOLS_PROJ_ROOT": str(proj_root)}
    res = _run(
        ["send", "--to-project", "alpha", "--subject", "Ping", "--body", "hi",
         "--from-project", "o", "--from-session", "s", "--from-uuid", "u",
         "--from-partition", "projects/o", "--to-partition", "projects/alpha"],
        tmp_path, proj_env,
    )
    assert res.returncode == 0, res.stderr

    res = _run(["list"], tmp_path, proj_env)
    assert res.returncode == 0
    assert "ago" in res.stdout or "just now" in res.stdout


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
         "--to-session", TARGET_UUID, "--subject", "Hi", "--body", "Body"],
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
    assert f"session={TARGET_UUID}" in glob_rows[0]
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


def _send_session(tmp_path: Path, ref: str, sessions_dir: Path) -> subprocess.CompletedProcess[str]:
    env = _send_env(tmp_path / "store", CLAUDE_CODE_SESSION_ID="s", CCST_SESSIONS_DIR=str(sessions_dir))
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "send",
         "--to-session", ref, "--subject", "Hi", "--body", "B"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )


def _list_rows(tmp_path: Path) -> list[str]:
    env = _send_env(tmp_path / "store")
    out = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "list"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    ).stdout
    return out.strip().splitlines()


def test_send_to_session_by_name_stores_the_full_uuid(tmp_path: Path) -> None:
    from cc_session_tools.lib import sessions_db

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    sessions_db.ensure_session_row(
        Path("/repos/p"), "20261005-example-tag", uuid=TARGET_UUID,
        path=sessions_dir / "sessions.db",
    )
    res = _send_session(tmp_path, "20261005-example-tag", sessions_dir)
    assert res.returncode == 0, res.stderr
    rows = _list_rows(tmp_path)
    assert len(rows) == 1 and f"session={TARGET_UUID}" in rows[0]


@pytest.mark.parametrize("ref", ["20260101-no-such-session", "a80f", "target-uuid"])
def test_send_to_unresolvable_session_exits_2_and_stores_nothing(tmp_path: Path, ref: str) -> None:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    res = _send_session(tmp_path, ref, sessions_dir)
    assert res.returncode == 2
    assert "ccmsg:" in res.stderr
    assert _list_rows(tmp_path) == []


def test_read_prints_sender_uuid_after_from_line(tmp_path: Path) -> None:
    sender = "a80f8695-1111-4222-8333-444444444444"
    res = _run(
        ["send", "--to-project", "alpha", "--subject", "Hi", "--body", "Body",
         "--from-project", "oneshot", "--from-session", "tag", "--from-uuid", sender,
         "--from-partition", "projects/oneshot", "--to-partition", "projects/alpha"],
        tmp_path,
    )
    lines = _run(["read", res.stdout.strip()], tmp_path).stdout.splitlines()
    from_index = next(i for i, line in enumerate(lines) if line.startswith("from:"))
    assert lines[from_index + 1] == f"from_uuid: {sender}"
