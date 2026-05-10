from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import cc_session_tools.lib.claude_flags as cf


SAMPLE_HELP = """
Usage: claude [options]

Options:
  --model <model>   Model to use
  --debug           Enable debug
  -p, --print       Print and exit
  --append-system-prompt <p>  Append system prompt
  -h, --help        Display help
"""


def test_get_claude_flags_parses_long_flags(tmp_path, monkeypatch):
    # Use a real file so Path.stat() works — avoids mock interference with mkdir(exist_ok=True)
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/bash")
    monkeypatch.setattr(cf, "_CACHE_FILE", tmp_path / "flags.json")
    monkeypatch.setattr(cf, "_CACHE_DIR", tmp_path)
    with patch("shutil.which", return_value=str(fake_claude)), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=SAMPLE_HELP, stderr="", returncode=0)
        flags = cf.get_claude_flags()
    assert "--model" in flags
    assert "--debug" in flags
    assert "--append-system-prompt" in flags
    assert "--help" in flags
    assert "-p" not in flags  # short flags excluded


def test_get_claude_flags_uses_cache(tmp_path, monkeypatch):
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/bash")
    real_mtime = fake_claude.stat().st_mtime
    cache_file = tmp_path / "flags.json"
    monkeypatch.setattr(cf, "_CACHE_FILE", cache_file)
    monkeypatch.setattr(cf, "_CACHE_DIR", tmp_path)
    cache_data = {"mtime": real_mtime, "path": str(fake_claude), "flags": ["--model", "--debug"]}
    cache_file.write_text(json.dumps(cache_data))
    with patch("shutil.which", return_value=str(fake_claude)), \
         patch("subprocess.run") as mock_run:
        flags = cf.get_claude_flags()
        mock_run.assert_not_called()
    assert "--model" in flags


def test_get_claude_flags_returns_empty_if_claude_missing(monkeypatch):
    with patch("shutil.which", return_value=None):
        flags = cf.get_claude_flags()
    assert flags == set()
