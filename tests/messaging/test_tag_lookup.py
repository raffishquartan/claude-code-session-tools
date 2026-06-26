# tests/messaging/test_tag_lookup.py
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from cc_session_tools.lib.messaging.tag_lookup import (
    SessionTagMatch,
    live_session_uuids,
    resolve_session_tag,
)


def _write_tag(tags_dir: Path, uuid: str, content: str) -> Path:
    f = tags_dir / f"{uuid}.tag"
    f.write_text(content)
    return f


def _make_sessions_dir(home: Path) -> Path:
    """Return the ~/.claude/sessions directory, creating it."""
    d = home / ".claude" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_registry(sessions_dir: Path, pid: int, uuid: str) -> None:
    (sessions_dir / f"{pid}.json").write_text(
        json.dumps({"pid": pid, "sessionId": uuid})
    )


# ---------------------------------------------------------------------------
# resolve_session_tag
# ---------------------------------------------------------------------------


def test_no_tags_dir_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path / "nonexistent"))
    assert resolve_session_tag("any-tag") == []


def test_single_match_trailing_newline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))
    uuid = "aaaa-bbbb-cccc-dddd"
    _write_tag(tmp_path, uuid, "my-tag\n")
    results = resolve_session_tag("my-tag")
    assert len(results) == 1
    assert results[0].uuid == uuid


def test_three_matches_same_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))
    for i in range(3):
        _write_tag(tmp_path, f"uuid-{i}", "shared-tag")
    results = resolve_session_tag("shared-tag")
    assert len(results) == 3


def test_no_match_on_different_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))
    _write_tag(tmp_path, "some-uuid", "other-tag")
    assert resolve_session_tag("my-tag") == []


def test_unreadable_tag_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))
    good = _write_tag(tmp_path, "good-uuid", "my-tag")
    bad = _write_tag(tmp_path, "bad-uuid", "my-tag")
    bad.chmod(0o000)
    try:
        results = resolve_session_tag("my-tag")
        # At least the readable one is returned; unreadable silently skipped
        uuids = {r.uuid for r in results}
        assert "good-uuid" in uuids
        assert "bad-uuid" not in uuids
    finally:
        bad.chmod(0o644)


def test_ordering_newer_mtime_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tmp_path))
    older = _write_tag(tmp_path, "uuid-old", "tag")
    time.sleep(0.02)
    newer = _write_tag(tmp_path, "uuid-new", "tag")
    results = resolve_session_tag("tag")
    assert len(results) == 2
    assert results[0].uuid == "uuid-new"
    assert results[1].uuid == "uuid-old"


# ---------------------------------------------------------------------------
# live_session_uuids
# ---------------------------------------------------------------------------


def test_live_pid_included(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_dir = _make_sessions_dir(tmp_path)
    _write_registry(sessions_dir, os.getpid(), "live-uuid-1")
    assert "live-uuid-1" in live_session_uuids()


def test_dead_pid_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_dir = _make_sessions_dir(tmp_path)
    _write_registry(sessions_dir, 999999999, "dead-uuid-1")
    assert "dead-uuid-1" not in live_session_uuids()


def test_missing_pid_field_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_dir = _make_sessions_dir(tmp_path)
    (sessions_dir / "nopid.json").write_text(json.dumps({"sessionId": "no-pid-uuid"}))
    assert "no-pid-uuid" not in live_session_uuids()


def test_missing_session_id_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_dir = _make_sessions_dir(tmp_path)
    (sessions_dir / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid()}))
    assert live_session_uuids() == set()


def test_non_dict_json_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_dir = _make_sessions_dir(tmp_path)
    (sessions_dir / "42.json").write_text("[1, 2, 3]")
    assert live_session_uuids() == set()


def test_no_sessions_dir_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    # no ~/.claude/sessions directory created
    assert live_session_uuids() == set()


# ---------------------------------------------------------------------------
# is_live flag via resolve_session_tag
# ---------------------------------------------------------------------------


def test_live_flag_set_for_running_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tags_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_dir = _make_sessions_dir(tmp_path)
    uuid = "live-match"
    _write_tag(tags_dir, uuid, "my-tag")
    _write_registry(sessions_dir, os.getpid(), uuid)
    results = resolve_session_tag("my-tag")
    assert len(results) == 1
    assert results[0].is_live is True


def test_live_flag_false_for_dead_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tags_dir = tmp_path / "tags"
    tags_dir.mkdir()
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tags_dir))
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_dir = _make_sessions_dir(tmp_path)
    uuid = "dead-match"
    _write_tag(tags_dir, uuid, "my-tag")
    _write_registry(sessions_dir, 999999999, uuid)
    results = resolve_session_tag("my-tag")
    assert len(results) == 1
    assert results[0].is_live is False
