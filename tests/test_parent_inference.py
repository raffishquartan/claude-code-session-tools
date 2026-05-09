"""Tests for parent session inference."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from claude_code_usage import parent_inference


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_record(content: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": content}}


def _hook_records(prefix: str | None = None) -> list[dict]:
    if prefix:
        content = f"{prefix}: Review this shell command for security risks and side effects. Reply in this EXACT format"
    else:
        content = "Review this shell command for security risks and side effects. Reply in this EXACT format"
    return [_user_record(content)]


def _regular_records() -> list[dict]:
    return [_user_record("Hello, please help me with a task.")]


# ---------------------------------------------------------------------------
# detect_session_type
# ---------------------------------------------------------------------------

def test_detect_session_type_subagent_path(tmp_path: Path) -> None:
    subagent_path = tmp_path / "-proj" / "abc-uuid" / "subagents" / "agent-a1b2c3.jsonl"
    subagent_path.parent.mkdir(parents=True)
    assert parent_inference.detect_session_type(subagent_path, []) == "subagent"


def test_detect_session_type_hook_first_message(tmp_path: Path) -> None:
    regular_path = tmp_path / "session.jsonl"
    result = parent_inference.detect_session_type(regular_path, _hook_records())
    assert result == "hook"


def test_detect_session_type_hook_with_prefix(tmp_path: Path) -> None:
    regular_path = tmp_path / "session.jsonl"
    result = parent_inference.detect_session_type(regular_path, _hook_records(prefix="20260509-my-session"))
    assert result == "hook"


def test_detect_session_type_regular(tmp_path: Path) -> None:
    regular_path = tmp_path / "session.jsonl"
    result = parent_inference.detect_session_type(regular_path, _regular_records())
    assert result == "regular"


def test_detect_session_type_no_user_records_is_regular(tmp_path: Path) -> None:
    result = parent_inference.detect_session_type(tmp_path / "s.jsonl", [])
    assert result == "regular"


def test_detect_session_type_subagent_takes_priority_over_records(tmp_path: Path) -> None:
    subagent_path = tmp_path / "proj" / "uuid" / "subagents" / "agent-x.jsonl"
    subagent_path.parent.mkdir(parents=True)
    # Even if the records look like a hook session, path wins
    result = parent_inference.detect_session_type(subagent_path, _hook_records())
    assert result == "subagent"


# ---------------------------------------------------------------------------
# extract_hook_parent_name
# ---------------------------------------------------------------------------

def test_extract_hook_parent_name_with_date_prefix() -> None:
    content = "20260509-my-session: Review this shell command for security risks and side effects."
    assert parent_inference.extract_hook_parent_name(content) == "20260509-my-session"


def test_extract_hook_parent_name_with_date_range_prefix() -> None:
    content = "20260501-to-20260509-my-session: Review this shell command for security risks and side effects."
    assert parent_inference.extract_hook_parent_name(content) == "20260501-to-20260509-my-session"


def test_extract_hook_parent_name_no_prefix() -> None:
    content = "Review this shell command for security risks and side effects."
    assert parent_inference.extract_hook_parent_name(content) is None


def test_extract_hook_parent_name_wrong_prefix_format() -> None:
    content = "some-random-thing: Review this shell command for security risks and side effects."
    assert parent_inference.extract_hook_parent_name(content) is None


# ---------------------------------------------------------------------------
# resolve_parents
# ---------------------------------------------------------------------------

def _make_df(**kwargs) -> pd.DataFrame:
    """Build a minimal DataFrame row for testing."""
    defaults = {
        "session_id": "s-1",
        "source_file": "/proj/s-1.jsonl",
        "session_type": "regular",
        "hook_parent_name": None,
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_5m": 0,
        "cache_creation_1h": 0,
        "cache_read": 0,
        "cost_usd": 0.01,
    }
    row = {**defaults, **kwargs}
    return pd.DataFrame([row])


def test_resolve_parents_regular_session_gets_null() -> None:
    df = _make_df(session_type="regular")
    result = parent_inference.resolve_parents(df, name_map={})
    assert result["parent_session_id"].iloc[0] is None or pd.isna(result["parent_session_id"].iloc[0])


def test_resolve_parents_hook_with_prefix_resolved_to_uuid() -> None:
    df = _make_df(session_type="hook", hook_parent_name="20260509-my-session")
    name_map = {"parent-uuid-1234": "20260509-my-session"}
    result = parent_inference.resolve_parents(df, name_map=name_map)
    assert result["parent_session_id"].iloc[0] == "parent-uuid-1234"


def test_resolve_parents_hook_with_prefix_not_in_map_gets_null() -> None:
    df = _make_df(session_type="hook", hook_parent_name="20260509-unknown-session")
    name_map = {"parent-uuid-1234": "20260509-my-session"}
    result = parent_inference.resolve_parents(df, name_map=name_map)
    assert result["parent_session_id"].iloc[0] is None or pd.isna(result["parent_session_id"].iloc[0])


def test_resolve_parents_hook_without_prefix_gets_null() -> None:
    df = _make_df(session_type="hook", hook_parent_name=None)
    name_map = {"parent-uuid-1234": "20260509-my-session"}
    result = parent_inference.resolve_parents(df, name_map=name_map)
    assert result["parent_session_id"].iloc[0] is None or pd.isna(result["parent_session_id"].iloc[0])


def test_resolve_parents_subagent_extracts_uuid_from_source_file() -> None:
    source = "/home/.claude/projects/-proj/parent-uuid-5678/subagents/agent-abc.jsonl"
    df = _make_df(session_type="subagent", source_file=source)
    result = parent_inference.resolve_parents(df, name_map={})
    assert result["parent_session_id"].iloc[0] == "parent-uuid-5678"


def test_resolve_parents_subagent_non_subagent_path_gets_null() -> None:
    df = _make_df(session_type="subagent", source_file="/proj/session.jsonl")
    result = parent_inference.resolve_parents(df, name_map={})
    assert result["parent_session_id"].iloc[0] is None or pd.isna(result["parent_session_id"].iloc[0])


def test_resolve_parents_mixed_dataframe() -> None:
    rows = [
        {"session_id": "s-regular", "source_file": "/proj/s-regular.jsonl",
         "session_type": "regular", "hook_parent_name": None},
        {"session_id": "s-hook", "source_file": "/proj/s-hook.jsonl",
         "session_type": "hook", "hook_parent_name": "20260509-my-session"},
        {"session_id": "s-parent", "source_file": "/proj/parent-uuid/subagents/agent-x.jsonl",
         "session_type": "subagent", "hook_parent_name": None},
    ]
    df = pd.DataFrame(rows)
    name_map = {"s-parent-real": "20260509-my-session"}
    result = parent_inference.resolve_parents(df, name_map=name_map)
    assert "parent_session_id" in result.columns
    regular_row = result[result["session_id"] == "s-regular"].iloc[0]
    hook_row = result[result["session_id"] == "s-hook"].iloc[0]
    subagent_row = result[result["session_id"] == "s-parent"].iloc[0]
    assert pd.isna(regular_row["parent_session_id"])
    assert hook_row["parent_session_id"] == "s-parent-real"
    assert subagent_row["parent_session_id"] == "parent-uuid"
