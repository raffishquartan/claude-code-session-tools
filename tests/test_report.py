"""Tests for the markdown report renderer."""

from __future__ import annotations

import pandas as pd

from claude_code_usage import report


def _row(**overrides) -> dict:
    base = {
        "ts": "2026-04-15T10:00:00Z",
        "session_id": "s-1",
        "message_id": "msg-1",
        "request_id": "req-1",
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


def test_render_report_returns_markdown_with_expected_sections(monkeypatch) -> None:
    # Don't shell out to the real ccusage CLI in unit tests.
    monkeypatch.setattr(report.cw, "is_available", lambda: False)

    df = pd.DataFrame(
        [
            _row(message_id=f"m{i}", project_name="oneshot", input_tokens=1_000_000, output_tokens=1_000_000)
            for i in range(3)
        ]
        + [
            _row(message_id=f"n{i}", project_name="cccs", model="claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=0)
            for i in range(2)
        ]
    )
    md = report.render_report(df, since="2026-04-01", until="2026-05-01")
    assert "# Claude Code usage report" in md
    assert "## Headline" in md
    assert "## By month" in md
    assert "## By project" in md
    assert "## By model" in md
    assert "## By MCP server" in md
    assert "## By plugin" in md
    assert "## By tool" in md
    assert "## By session" in md
    assert "oneshot" in md
    assert "claude-opus-4-7" in md
