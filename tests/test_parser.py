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
        "cwd": "/mnt/c/Users/cfoge/OneDrive/claude/oneshot",
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
    assert row["project_cwd"] == "/mnt/c/Users/cfoge/OneDrive/claude/oneshot"
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


# --- parse_session_metadata tests ---

def _make_jsonl(records: list[dict], path: Path) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _custom_title_record(session_id: str, title: str) -> dict:
    return {"type": "custom-title", "customTitle": title, "sessionId": session_id}


def _user_record(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _user_record_list_content(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def test_parse_session_metadata_extracts_custom_title(tmp_path: Path) -> None:
    p = _make_jsonl(
        [_custom_title_record("uuid-abc", "20260509-my-session"), _good_record()],
        tmp_path / "s.jsonl",
    )
    meta = parser.parse_session_metadata(p)
    assert meta["custom_title"] == "20260509-my-session"


def test_parse_session_metadata_no_custom_title_returns_none(tmp_path: Path) -> None:
    p = _make_jsonl([_good_record()], tmp_path / "s.jsonl")
    meta = parser.parse_session_metadata(p)
    assert meta["custom_title"] is None


def test_parse_session_metadata_is_sidechain_true(tmp_path: Path) -> None:
    sidechain_record = _good_record() | {"isSidechain": True}
    p = _make_jsonl([sidechain_record], tmp_path / "s.jsonl")
    meta = parser.parse_session_metadata(p)
    assert meta["is_sidechain"] is True


def test_parse_session_metadata_is_sidechain_false_by_default(tmp_path: Path) -> None:
    p = _make_jsonl([_good_record()], tmp_path / "s.jsonl")
    meta = parser.parse_session_metadata(p)
    assert meta["is_sidechain"] is False


def test_parse_session_metadata_hook_initiation_type(tmp_path: Path) -> None:
    p = _make_jsonl(
        [_user_record("Review this shell command for security risks: rm -rf /"), _good_record()],
        tmp_path / "s.jsonl",
    )
    meta = parser.parse_session_metadata(p)
    assert meta["initiation_type"] == "hook-security-review"


def test_parse_session_metadata_prompt_file_here_is_your_prompt(tmp_path: Path) -> None:
    p = _make_jsonl(
        [_user_record("Here is your prompt for this session: do stuff"), _good_record()],
        tmp_path / "s.jsonl",
    )
    meta = parser.parse_session_metadata(p)
    assert meta["initiation_type"] == "prompt-file"


def test_parse_session_metadata_prompt_file_your_prompt_colon(tmp_path: Path) -> None:
    p = _make_jsonl(
        [_user_record("Your prompt: do something"), _good_record()],
        tmp_path / "s.jsonl",
    )
    meta = parser.parse_session_metadata(p)
    assert meta["initiation_type"] == "prompt-file"


def test_parse_session_metadata_interactive_initiation(tmp_path: Path) -> None:
    p = _make_jsonl(
        [_user_record("Can you help me with this task?"), _good_record()],
        tmp_path / "s.jsonl",
    )
    meta = parser.parse_session_metadata(p)
    assert meta["initiation_type"] == "interactive"


def test_parse_session_metadata_unknown_when_no_user_messages(tmp_path: Path) -> None:
    p = _make_jsonl([_good_record()], tmp_path / "s.jsonl")
    meta = parser.parse_session_metadata(p)
    assert meta["initiation_type"] == "unknown"


def test_parse_session_metadata_skips_harness_local_command_prefix(tmp_path: Path) -> None:
    p = _make_jsonl(
        [
            _user_record("<local-command-stdout>some output</local-command-stdout>"),
            _user_record("Can you help me?"),
            _good_record(),
        ],
        tmp_path / "s.jsonl",
    )
    meta = parser.parse_session_metadata(p)
    assert meta["initiation_type"] == "interactive"


def test_parse_session_metadata_skips_command_name_prefix(tmp_path: Path) -> None:
    p = _make_jsonl(
        [
            _user_record("<command-name>bash</command-name>"),
            _user_record("Review this shell command for security risks: ls"),
            _good_record(),
        ],
        tmp_path / "s.jsonl",
    )
    meta = parser.parse_session_metadata(p)
    assert meta["initiation_type"] == "hook-security-review"


def test_parse_session_metadata_handles_list_content_text_block(tmp_path: Path) -> None:
    p = _make_jsonl(
        [_user_record_list_content("Here is your prompt: do something"), _good_record()],
        tmp_path / "s.jsonl",
    )
    meta = parser.parse_session_metadata(p)
    assert meta["initiation_type"] == "prompt-file"


def test_parse_session_metadata_returns_unknown_on_malformed_file(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text("{{{ not json at all\n")
    meta = parser.parse_session_metadata(p)
    assert meta["initiation_type"] == "unknown"
    assert meta["custom_title"] is None
    assert meta["is_sidechain"] is False


def test_parse_session_metadata_first_prompt_is_80_chars(tmp_path: Path) -> None:
    long_text = "Can you help me with this very long task that goes on and on for many characters?"
    p = _make_jsonl([_user_record(long_text), _good_record()], tmp_path / "s.jsonl")
    meta = parser.parse_session_metadata(p)
    assert len(meta["first_prompt"]) <= 80
    assert meta["first_prompt"] == long_text[:80]


def test_parse_session_metadata_first_prompt_none_when_no_user(tmp_path: Path) -> None:
    p = _make_jsonl([_good_record()], tmp_path / "s.jsonl")
    meta = parser.parse_session_metadata(p)
    assert meta["first_prompt"] is None
