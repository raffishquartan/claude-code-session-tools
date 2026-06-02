"""Tests for the query layer."""

from __future__ import annotations

import pandas as pd
import pytest

from claude_code_usage import query


def _row(**overrides) -> dict:
    base = {
        "ts": "2026-04-15T10:00:00Z",
        "session_id": "s-1",
        "project_cwd": "/x/oneshot",
        "project_name": "oneshot",
        "git_branch": "main",
        "model": "claude-opus-4-7",
        "service_tier": "standard",
        "input_tokens": 1000,
        "cache_creation_5m": 0,
        "cache_creation_1h": 0,
        "cache_read": 0,
        "output_tokens": 1000,
        "web_search_count": 0,
        "web_fetch_count": 0,
        "tool_calls": [],
        "uuid": "u-x",
        "version": "2.0.0",
        "source_file": "/x/a.jsonl",
    }
    base.update(overrides)
    return base


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_run_query_no_filters_no_groupby_returns_single_total_row() -> None:
    df = _df(
        [
            _row(input_tokens=1_000_000, output_tokens=1_000_000),
            _row(input_tokens=1_000_000, output_tokens=1_000_000, model="claude-sonnet-4-6"),
        ]
    )
    result = query.run_query(df)
    assert len(result) == 1
    assert result.iloc[0]["total_tokens"] == 4_000_000
    assert result.iloc[0]["message_count"] == 2
    # Opus: 1M input ($15) + 1M output ($75) = $90
    # Sonnet: 1M input ($3) + 1M output ($15) = $18
    # Total: $108
    assert result.iloc[0]["cost_usd"] == 108.0


def test_filter_by_project_returns_only_matching_rows() -> None:
    df = _df(
        [
            _row(project_name="oneshot", input_tokens=1_000_000, output_tokens=0),
            _row(project_name="cccs", input_tokens=2_000_000, output_tokens=0),
        ]
    )
    result = query.run_query(df, project="oneshot")
    assert result.iloc[0]["input_tokens"] == 1_000_000


def test_time_filter_excludes_outside_range() -> None:
    df = _df(
        [
            _row(ts="2026-03-31T23:59:00Z", input_tokens=1_000_000, output_tokens=0),
            _row(ts="2026-04-01T00:00:00Z", input_tokens=2_000_000, output_tokens=0),
            _row(ts="2026-05-01T00:00:00Z", input_tokens=4_000_000, output_tokens=0),
        ]
    )
    result = query.run_query(df, since="2026-04-01", until="2026-05-01")
    assert result.iloc[0]["input_tokens"] == 2_000_000


def test_group_by_model_returns_one_row_per_model() -> None:
    df = _df(
        [
            _row(model="claude-opus-4-7", input_tokens=1_000_000, output_tokens=0),
            _row(model="claude-opus-4-7", input_tokens=1_000_000, output_tokens=0),
            _row(model="claude-sonnet-4-6", input_tokens=2_000_000, output_tokens=0),
        ]
    )
    result = query.run_query(df, group_by=["model"]).set_index("model")
    assert result.loc["claude-opus-4-7", "input_tokens"] == 2_000_000
    assert result.loc["claude-sonnet-4-6", "input_tokens"] == 2_000_000


def test_group_by_month_buckets_by_calendar_month() -> None:
    df = _df(
        [
            _row(ts="2026-04-15T10:00:00Z", input_tokens=1_000_000, output_tokens=0),
            _row(ts="2026-04-30T23:59:00Z", input_tokens=2_000_000, output_tokens=0),
            _row(ts="2026-05-01T00:00:00Z", input_tokens=4_000_000, output_tokens=0),
        ]
    )
    result = query.run_query(df, group_by=["month"]).set_index("month")
    assert result.loc["2026-04", "input_tokens"] == 3_000_000
    assert result.loc["2026-05", "input_tokens"] == 4_000_000


def test_group_by_project_and_model_cells() -> None:
    df = _df(
        [
            _row(project_name="oneshot", model="claude-opus-4-7", input_tokens=1_000_000, output_tokens=0),
            _row(project_name="oneshot", model="claude-sonnet-4-6", input_tokens=2_000_000, output_tokens=0),
            _row(project_name="cccs", model="claude-opus-4-7", input_tokens=4_000_000, output_tokens=0),
        ]
    )
    result = query.run_query(df, group_by=["project", "model"])
    assert len(result) == 3


def test_group_by_tool_explodes_and_attributes() -> None:
    df = _df(
        [
            # message with 2 tool uses; output_tokens=400 -> 200 each
            _row(
                tool_calls=["Bash", "mcp__opentabs__t1"],
                input_tokens=0,
                output_tokens=400,
                model="claude-opus-4-7",
            ),
        ]
    )
    result = query.run_query(df, group_by=["tool"]).set_index("tool")
    assert result.loc["Bash", "output_tokens"] == 200.0
    assert result.loc["mcp__opentabs__t1", "output_tokens"] == 200.0


def test_group_by_mcp_aggregates_per_server() -> None:
    df = _df(
        [
            _row(
                tool_calls=["mcp__opentabs__a", "mcp__opentabs__b"],
                input_tokens=0,
                output_tokens=400,
                model="claude-opus-4-7",
            ),
            _row(
                tool_calls=["mcp__whatsapp__a"],
                input_tokens=0,
                output_tokens=400,
                model="claude-opus-4-7",
            ),
        ]
    )
    result = query.run_query(df, group_by=["mcp"]).set_index("identifier")
    assert result.loc["opentabs", "output_tokens"] == 400.0
    assert result.loc["whatsapp", "output_tokens"] == 400.0


def test_include_children_folds_child_tokens_into_parent_row() -> None:
    df = _df(
        [
            _row(session_id="parent-s", input_tokens=1000, output_tokens=500,
                 parent_session_id=None),
            _row(session_id="child-hook", input_tokens=5000, output_tokens=2000,
                 parent_session_id="parent-s"),
        ]
    )
    result = query.run_query(df, group_by=["session"], include_children=True)
    assert len(result) == 1
    parent = result[result["session_count"] == "parent-s"].iloc[0]
    assert parent["child_session_count"] == 1
    assert parent["child_token_total"] == 7000  # 5000 + 2000
    assert parent["child_cost_usd"] >= 0.0


def test_include_children_removes_child_rows_from_output() -> None:
    df = _df(
        [
            _row(session_id="parent-s", input_tokens=1000, output_tokens=500,
                 parent_session_id=None),
            _row(session_id="child-hook", input_tokens=5000, output_tokens=2000,
                 parent_session_id="parent-s"),
        ]
    )
    result = query.run_query(df, group_by=["session"], include_children=True)
    assert "child-hook" not in result["session_count"].values


def test_include_children_false_leaves_all_rows_present() -> None:
    df = _df(
        [
            _row(session_id="parent-s", input_tokens=1000, output_tokens=500,
                 parent_session_id=None),
            _row(session_id="child-hook", input_tokens=5000, output_tokens=2000,
                 parent_session_id="parent-s"),
        ]
    )
    result = query.run_query(df, group_by=["session"], include_children=False)
    session_ids = set(result["session_count"].values)
    assert "parent-s" in session_ids
    assert "child-hook" in session_ids
    assert "child_session_count" not in result.columns


def test_include_children_no_session_groupby_is_noop() -> None:
    df = _df(
        [
            _row(session_id="parent-s", input_tokens=1000, output_tokens=500,
                 parent_session_id=None, project_name="proj-a"),
            _row(session_id="child-hook", input_tokens=5000, output_tokens=2000,
                 parent_session_id="parent-s", project_name="proj-a"),
        ]
    )
    result = query.run_query(df, group_by=["project"], include_children=True)
    # group-by project only, no session → folding is a no-op
    assert "child_session_count" not in result.columns


def test_include_children_orphan_sessions_get_zero_child_columns() -> None:
    df = _df(
        [
            _row(session_id="solo-s", input_tokens=1000, output_tokens=500,
                 parent_session_id=None),
        ]
    )
    result = query.run_query(df, group_by=["session"], include_children=True)
    solo = result[result["session_count"] == "solo-s"].iloc[0]
    assert solo["child_session_count"] == 0
    assert solo["child_token_total"] == 0


def test_group_by_session_and_tool_returns_expected_rows() -> None:
    df = _df(
        [
            _row(session_id="s-1", tool_calls=["Bash", "Read"]),
            _row(session_id="s-1", tool_calls=[]),
            _row(session_id="s-2", tool_calls=["Edit"]),
        ]
    )
    result = query.run_query(df, group_by=["session", "tool"])
    assert len(result) > 0
    tools = set(result["tool"])
    assert "Bash" in tools
    assert "Read" in tools
    assert "Edit" in tools


def test_group_by_session_and_tool_no_tool_calls_column_does_not_crash() -> None:
    """Stale parquet cache rows may lack tool_calls; _aggregate must not KeyError."""
    rows = [_row(session_id="s-1", tool_calls=["Bash"])]
    df = pd.DataFrame(rows).drop(columns=["tool_calls"])
    # needs_tool_explode is True but _explode_by_tools receives a df missing
    # tool_calls; the resulting exploded frame treats all rows as <no-tool>
    # and adds tool_call_count.  _aggregate must handle that frame cleanly.
    result = query.run_query(df, group_by=["session", "tool"])
    assert len(result) >= 1


def test_filter_by_mcp_keeps_only_that_server() -> None:
    df = _df(
        [
            _row(tool_calls=["mcp__opentabs__a"], output_tokens=400, model="claude-opus-4-7"),
            _row(tool_calls=["mcp__whatsapp__a"], output_tokens=400, model="claude-opus-4-7"),
        ]
    )
    result = query.run_query(df, mcp="opentabs", group_by=["tool"])
    assert len(result) == 1
    assert "opentabs" in result.iloc[0]["tool"]
