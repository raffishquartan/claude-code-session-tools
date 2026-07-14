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
    monkeypatch.setenv("CCST_CLAUDE_FLAGS_DIR", str(tmp_path))
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
    monkeypatch.setenv("CCST_CLAUDE_FLAGS_DIR", str(tmp_path))
    cache_file = tmp_path / "claude-flags.json"
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


def test_default_cache_dir_uses_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("CCST_CLAUDE_FLAGS_DIR", raising=False)
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    assert cf._cache_file() == tmp_path / "claude-flags.json"


def test_get_claude_flags_writes_cache_atomically(tmp_path, monkeypatch):
    """Regression test for the non-atomic write bugfix: no leftover .tmp file, valid content."""
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/bash")
    monkeypatch.setenv("CCST_CLAUDE_FLAGS_DIR", str(tmp_path))
    with patch("shutil.which", return_value=str(fake_claude)), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=SAMPLE_HELP, stderr="", returncode=0)
        cf.get_claude_flags()

    cache_file = tmp_path / "claude-flags.json"
    assert cache_file.exists()
    assert not cache_file.with_suffix(".tmp").exists()
    written = json.loads(cache_file.read_text())
    assert "--model" in written["flags"]
    assert written["path"] == str(fake_claude)
