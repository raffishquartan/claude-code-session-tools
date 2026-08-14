import json
import sys
from pathlib import Path
from unittest.mock import patch

import analyze_context

FIX = Path(__file__).parent / "fixtures"


def test_end_to_end(tmp_path):
    argv = ["analyze_context.py",
            "--claude-home", str(FIX / "claude_home"),
            "--project-root", str(FIX / "project_root"),
            "--captured", str(FIX / "captured-context.txt"),
            "--out", str(tmp_path)]
    with patch.object(sys, "argv", argv), \
         patch("usage.query_usage", return_value={}):
        analyze_context.main()
    data = json.loads((tmp_path / "context-report.json").read_text())
    assert data["total_tokens"] > 0
    assert (tmp_path / "context-report.md").exists()
    names = {c["name"] for c in data["contributors"]}
    assert "CLAUDE.md (global)" in names
    assert "CLAUDE.md (project)" in names


def test_end_to_end_without_project_root(tmp_path):
    """--project-root is optional; omitting it must not error or add a row."""
    argv = ["analyze_context.py",
            "--claude-home", str(FIX / "claude_home"),
            "--captured", str(FIX / "captured-context.txt"),
            "--out", str(tmp_path)]
    with patch.object(sys, "argv", argv), \
         patch("usage.query_usage", return_value={}):
        analyze_context.main()
    data = json.loads((tmp_path / "context-report.json").read_text())
    names = {c["name"] for c in data["contributors"]}
    assert "CLAUDE.md (project)" not in names
