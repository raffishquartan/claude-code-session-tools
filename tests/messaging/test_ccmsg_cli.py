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
    from cc_session_tools.lib.messaging.message import parse

    env = _send_env(tmp_path, CLAUDE_CODE_SESSION_ID="env-sender-uuid")
    res = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "send",
         "--to-session", "target-uuid", "--subject", "Hi", "--body", "Body"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert res.returncode == 0, res.stderr
    files = list((tmp_path / "_global" / "inbox").glob("*.md"))
    assert len(files) == 1
    m = parse(files[0].read_text(encoding="utf-8"))
    assert m.from_uuid == "env-sender-uuid"  # taken from $CLAUDE_CODE_SESSION_ID
    assert m.to_kind == "session" and m.to_value == "target-uuid"
    assert m.to_location == "_global"


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
    assert list((store_dir / "projects" / "alpha" / "inbox").glob("*.md"))


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


# ---------------------------------------------------------------------------
# --to-session-tag tests
# ---------------------------------------------------------------------------


def _tag_env(store_root: Path, tags_dir: Path, sessions_dir_parent: Path,
             sender_uuid: str, **extra: str) -> dict[str, str]:
    """Build env dict wiring both stores and the session-tags dir."""
    env = dict(os.environ)
    env["CCST_MESSAGES_ROOT"] = str(store_root)
    env["CCCS_SESSION_TAGS_DIR"] = str(tags_dir)
    env["HOME"] = str(sessions_dir_parent)
    env["CLAUDE_CODE_SESSION_ID"] = sender_uuid
    env.pop("CLAUDE_SESSION_TOOLS_PROJ_ROOT", None)
    env.pop("CLAUDE_SESSION_TOOLS_REPO_ROOT", None)
    env.update(extra)
    return env


def _write_tag(tags_dir: Path, uuid: str, tag: str) -> None:
    (tags_dir / f"{uuid}.tag").write_text(tag + "\n")


def _write_registry(home: Path, pid: int, uuid: str) -> None:
    sessions_dir = home / ".claude" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": uuid}))


def _send_via_tag(home: Path, env: dict[str, str], tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "send",
         "--to-session-tag", tag, "--subject", "Test", "--body", "Hello"],
        capture_output=True, text=True, env=env, cwd=str(home),
    )


def test_send_via_tag_single_live_match(tmp_path: Path) -> None:
    """Single live match → message written to _global/inbox with correct routing."""
    from cc_session_tools.lib.messaging.message import parse

    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    store = tmp_path / "store"
    target_uuid = "target-live-uuid"
    sender_uuid = "sender-uuid"

    _write_tag(tags_dir, target_uuid, "my-feature")
    _write_registry(tmp_path, os.getpid(), target_uuid)

    env = _tag_env(store, tags_dir, tmp_path, sender_uuid)
    res = _send_via_tag(tmp_path, env, "my-feature")
    assert res.returncode == 0, res.stderr

    files = list((store / "_global" / "inbox").glob("*.md"))
    assert len(files) == 1
    m = parse(files[0].read_text(encoding="utf-8"))
    assert m.to_kind == "session"
    assert m.to_value == target_uuid
    assert m.to_location == "_global"


def test_send_via_tag_multiple_live_exits_2(tmp_path: Path) -> None:
    """Multiple live matches → exit 2, stderr lists --to-session candidates."""
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    store = tmp_path / "store"
    # Two live sessions with distinct alive pids (current process + parent)
    pairs = [("live-a", os.getpid()), ("live-b", os.getppid())]

    for uuid, pid in pairs:
        _write_tag(tags_dir, uuid, "shared-tag")
        _write_registry(tmp_path, pid, uuid)

    env = _tag_env(store, tags_dir, tmp_path, "sender-uuid")
    res = _send_via_tag(tmp_path, env, "shared-tag")
    assert res.returncode == 2
    combined = res.stderr + res.stdout
    assert "--to-session" in combined
    # No message file written
    assert not (store / "_global" / "inbox").exists()


def test_send_via_tag_zero_match_exits_2(tmp_path: Path) -> None:
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    store = tmp_path / "store"

    env = _tag_env(store, tags_dir, tmp_path, "sender-uuid")
    res = _send_via_tag(tmp_path, env, "nonexistent-tag")
    assert res.returncode == 2
    assert "no session" in (res.stderr + res.stdout).lower()


def test_send_via_tag_all_stale_exits_2_with_no_live_wording(tmp_path: Path) -> None:
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    store = tmp_path / "store"

    _write_tag(tags_dir, "stale-uuid", "old-tag")
    _write_registry(tmp_path, 999999999, "stale-uuid")  # dead pid

    env = _tag_env(store, tags_dir, tmp_path, "sender-uuid")
    res = _send_via_tag(tmp_path, env, "old-tag")
    assert res.returncode == 2
    assert "no live session" in (res.stderr + res.stdout).lower()


def test_send_via_tag_self_send_exits_2(tmp_path: Path) -> None:
    """Tag resolves to the sender's own uuid → rejected."""
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    store = tmp_path / "store"
    my_uuid = "my-own-uuid"

    _write_tag(tags_dir, my_uuid, "self-tag")
    _write_registry(tmp_path, os.getpid(), my_uuid)

    env = _tag_env(store, tags_dir, tmp_path, my_uuid)
    res = _send_via_tag(tmp_path, env, "self-tag")
    assert res.returncode == 2
    assert "this session" in (res.stderr + res.stdout).lower()


def test_send_via_tag_plus_to_project_exits_2(tmp_path: Path) -> None:
    """--to-session-tag combined with another recipient flag → exactly-one error."""
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    store = tmp_path / "store"

    env = _tag_env(store, tags_dir, tmp_path, "sender-uuid")
    res = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "send",
         "--to-session-tag", "some-tag", "--to-project", "alpha",
         "--subject", "Test", "--body", "Hello"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert res.returncode == 2
    assert "exactly one" in (res.stderr + res.stdout).lower()


def test_send_via_tag_empty_string_exits_2(tmp_path: Path) -> None:
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    store = tmp_path / "store"

    env = _tag_env(store, tags_dir, tmp_path, "sender-uuid")
    res = subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccmsg", "send",
         "--to-session-tag", "", "--subject", "Test", "--body", "Hello"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )
    assert res.returncode == 2


def test_send_via_tag_stale_matches_logged_to_stderr(tmp_path: Path) -> None:
    """Single live match with stale sibling → informational note on stderr."""
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    store = tmp_path / "store"
    live_uuid = "live-uuid"
    stale_uuid = "stale-uuid"

    _write_tag(tags_dir, live_uuid, "shared-tag")
    _write_tag(tags_dir, stale_uuid, "shared-tag")
    _write_registry(tmp_path, os.getpid(), live_uuid)
    _write_registry(tmp_path, 999999999, stale_uuid)

    env = _tag_env(store, tags_dir, tmp_path, "sender-uuid")
    res = _send_via_tag(tmp_path, env, "shared-tag")
    assert res.returncode == 0, res.stderr
    assert "stale" in res.stderr.lower()
