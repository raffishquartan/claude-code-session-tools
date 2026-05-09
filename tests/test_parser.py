"""Tests for the JSONL parser."""

from __future__ import annotations

import json
from pathlib import Path

from claude_code_usage import parser


def _good_record() -> dict:
    return {
        "type": "assistant",
        "uuid": "u-1",
        "sessionId": "s-1",
        "timestamp": "2026-05-09T10:36:44.339Z",
        "cwd": "/mnt/c/Users/alice/OneDrive/claude/oneshot",
        "gitBranch": "main",
        "version": "2.0.0",
        "message": {
            "model": "claude-opus-4-7",
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "name": "Bash", "id": "t1"},
            ],
            "usage": {
                "input_tokens": 6,
                "cache_creation_input_tokens": 29911,
                "cache_read_input_tokens": 18460,
                "output_tokens": 68,
                "service_tier": "standard",
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": 29911,
                },
                "server_tool_use": {
                    "web_search_requests": 2,
                    "web_fetch_requests": 1,
                },
            },
        },
    }


def test_parse_record_normalises_billable_record() -> None:
    row = parser.parse_record(_good_record())
    assert row is not None
    assert row["session_id"] == "s-1"
    assert row["project_cwd"] == "/mnt/c/Users/alice/OneDrive/claude/oneshot"
    assert row["project_name"] == "oneshot"
    assert row["model"] == "claude-opus-4-7"
    assert row["input_tokens"] == 6
    assert row["output_tokens"] == 68
    assert row["cache_read"] == 18460
    assert row["cache_creation_5m"] == 0
    assert row["cache_creation_1h"] == 29911
    assert row["service_tier"] == "standard"
    assert row["web_search_count"] == 2
    assert row["web_fetch_count"] == 1
    assert row["tool_calls"] == ["Bash"]
    assert row["git_branch"] == "main"


def test_parse_record_returns_none_for_non_billable() -> None:
    assert parser.parse_record({"type": "user", "message": {"role": "user"}}) is None
    assert parser.parse_record({"type": "queue-operation"}) is None


def test_parse_file_reads_jsonl_and_skips_noise(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    lines = [
        {"type": "queue-operation", "operation": "enqueue"},
        _good_record(),
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        _good_record() | {"uuid": "u-2"},
    ]
    p.write_text("\n".join(json.dumps(rec) for rec in lines) + "\n")
    rows = list(parser.parse_file(p))
    assert len(rows) == 2
    assert {r["uuid"] for r in rows} == {"u-1", "u-2"}


def test_parse_file_handles_blank_lines_and_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    p.write_text(
        "\n"
        + json.dumps(_good_record()) + "\n"
        + "{ this is not json }\n"
        + "\n"
    )
    rows = list(parser.parse_file(p))
    assert len(rows) == 1


def test_parse_file_adds_session_type_regular(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    p.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}) + "\n"
        + json.dumps(_good_record()) + "\n"
    )
    rows = list(parser.parse_file(p))
    assert rows[0]["session_type"] == "regular"
    assert rows[0]["hook_parent_name"] is None


def test_parse_file_adds_session_type_hook(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    hook_user = {
        "type": "user",
        "message": {"role": "user", "content": "Review this shell command for security risks and side effects."},
    }
    p.write_text(
        json.dumps(hook_user) + "\n"
        + json.dumps(_good_record()) + "\n"
    )
    rows = list(parser.parse_file(p))
    assert rows[0]["session_type"] == "hook"
    assert rows[0]["hook_parent_name"] is None


def test_parse_file_adds_hook_parent_name_when_prefixed(tmp_path: Path) -> None:
    p = tmp_path / "session.jsonl"
    hook_user = {
        "type": "user",
        "message": {
            "role": "user",
            "content": "20260509-my-session: Review this shell command for security risks and side effects.",
        },
    }
    p.write_text(
        json.dumps(hook_user) + "\n"
        + json.dumps(_good_record()) + "\n"
    )
    rows = list(parser.parse_file(p))
    assert rows[0]["session_type"] == "hook"
    assert rows[0]["hook_parent_name"] == "20260509-my-session"


def test_parse_file_adds_session_type_subagent(tmp_path: Path) -> None:
    subagent_dir = tmp_path / "parent-uuid" / "subagents"
    subagent_dir.mkdir(parents=True)
    p = subagent_dir / "agent-abc123.jsonl"
    p.write_text(json.dumps(_good_record()) + "\n")
    rows = list(parser.parse_file(p))
    assert rows[0]["session_type"] == "subagent"
    assert rows[0]["hook_parent_name"] is None


def test_parse_directory_walks_all_jsonl_files(tmp_path: Path) -> None:
    proj_a = tmp_path / "-home-a"
    proj_b = tmp_path / "-home-b"
    proj_a.mkdir()
    proj_b.mkdir()
    (proj_a / "session1.jsonl").write_text(json.dumps(_good_record()) + "\n")
    (proj_a / "session2.jsonl").write_text(json.dumps(_good_record() | {"uuid": "u-2"}) + "\n")
    (proj_b / "session3.jsonl").write_text(json.dumps(_good_record() | {"uuid": "u-3"}) + "\n")
    (proj_a / "ignore.txt").write_text("not jsonl")
    rows = list(parser.parse_directory(tmp_path))
    assert len(rows) == 3
