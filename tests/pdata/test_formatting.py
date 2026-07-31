from __future__ import annotations

import json

from cc_session_tools.lib.pdata import formatting

_ROWS = [
    {"id": 1, "content": "first", "sender": "alice"},
    {"id": 2, "content": "second", "sender": None},
]


def test_render_table_includes_headers_and_values():
    out = formatting.render(_ROWS, fmt="table")
    assert "id" in out and "content" in out and "sender" in out
    assert "first" in out
    assert "alice" in out


def test_render_json_round_trips():
    out = formatting.render(_ROWS, fmt="json")
    parsed = json.loads(out)
    assert parsed == _ROWS


def test_render_csv_has_header_and_rows():
    out = formatting.render(_ROWS, fmt="csv")
    lines = out.strip().splitlines()
    assert lines[0] == "id,content,sender"
    assert lines[1] == "1,first,alice"
    assert lines[2] == "2,second,"


def test_render_empty_list_table():
    assert "No rows" in formatting.render([], fmt="table")


def test_render_empty_list_json():
    assert json.loads(formatting.render([], fmt="json")) == []


def test_render_unknown_format_raises():
    import pytest
    with pytest.raises(ValueError, match="format"):
        formatting.render(_ROWS, fmt="xml")
