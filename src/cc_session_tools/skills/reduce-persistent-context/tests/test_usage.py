import json
from unittest.mock import patch

import usage

SAMPLE = json.dumps([
    {"identifier": "opentabs", "tool_call_count": 4706},
    {"identifier": "google-workspace", "tool_call_count": 85},
])


def test_parses_call_counts():
    with patch("usage._run", return_value=SAMPLE):
        result = usage.query_usage(group_by="mcp", since="2026-03-22")
    assert result == {"opentabs": 4706, "google-workspace": 85}


def test_tool_dimension_uses_tool_key():
    sample = json.dumps([{"tool": "Bash", "tool_call_count": 1200}])
    with patch("usage._run", return_value=sample):
        result = usage.query_usage(group_by="tool", since=None)
    assert result == {"Bash": 1200}


def test_missing_cli_returns_empty():
    with patch("usage._run", side_effect=FileNotFoundError):
        assert usage.query_usage(group_by="mcp", since=None) == {}
