"""Tests for ccr --include-orphans functionality.

TDD order:
  1. Without --include-orphans, orphan transcripts are NOT returned.
  2. With --include-orphans, a fixture orphan IS returned (is_orphan=True).
  3. With --include-orphans, on-disk match wins over orphan (no duplicate).
  4. Printed/picker label shows [orphan] for orphan entries.
  5. Unresolvable orphan (no name-cache entry) is silently skipped.
  6. Selecting an orphan in picker execs claude --resume <basename>.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_session_tools.cli import ccr
from cc_session_tools.lib.sessions import SessionMatch


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_session(repos: Path, project: str, basename: str) -> Path:
    sess = repos / project / "cc-sessions" / basename
    (sess / "working").mkdir(parents=True)
    (sess / "out").mkdir()
    return sess


def _write_jsonl(claude_projects_dir: Path, encoded_cwd: str, uuid: str,
                 display_name: str) -> Path:
    """Write a minimal JSONL transcript with a custom-title record."""
    proj_dir = claude_projects_dir / encoded_cwd
    proj_dir.mkdir(parents=True, exist_ok=True)
    jsonl = proj_dir / f"{uuid}.jsonl"
    record = {"type": "custom-title", "customTitle": display_name, "sessionId": uuid}
    jsonl.write_text(json.dumps(record) + "\n")
    return jsonl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _claude_on_path(monkeypatch):
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: "/usr/bin/claude")


@pytest.fixture
def captured_launch(monkeypatch):
    captured: dict = {}

    def fake_launch(cmd, env, cwd=None):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env)
        captured["cwd"] = cwd

    monkeypatch.setattr(ccr, "launch_claude_resume", fake_launch)
    return captured


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def fake_repos(fake_home, tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    repos.mkdir()
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos))
    return repos


@pytest.fixture
def claude_projects(fake_home):
    """Return the ~/.claude/projects dir, creating it."""
    d = fake_home / ".claude" / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Test 1: Without --include-orphans, orphan transcripts are NOT returned
# ---------------------------------------------------------------------------

def test_no_orphans_without_flag(fake_repos, claude_projects, captured_launch):
    """An orphan transcript must NOT appear when --include-orphans is absent."""
    # Create a project with no cc-sessions dir (so any transcript is an orphan)
    proj = fake_repos / "myproj"
    proj.mkdir()
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    _write_jsonl(claude_projects, encoded, "uuid-aaa", "20260504-orphan-session")

    # Run ccr without the flag - should find nothing
    rc = ccr.main(["orphan-session"])
    assert rc == 1  # no match found
    assert "cmd" not in captured_launch


# ---------------------------------------------------------------------------
# Test 2: With --include-orphans, a fixture orphan IS returned (is_orphan=True)
# ---------------------------------------------------------------------------

def test_orphan_returned_with_flag(fake_repos, claude_projects, captured_launch):
    """An orphan transcript IS returned when --include-orphans is passed."""
    proj = fake_repos / "myproj"
    proj.mkdir()
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    _write_jsonl(claude_projects, encoded, "uuid-bbb", "20260504-orphan-session")

    rc = ccr.main(["--include-orphans", "orphan-session"])
    assert rc == 0
    assert "cmd" in captured_launch
    assert "20260504-orphan-session" in captured_launch["cmd"]


def test_find_orphan_transcripts_returns_is_orphan_true(fake_repos, claude_projects):
    """find_orphan_transcripts yields SessionMatch with is_orphan=True."""
    from cc_session_tools.lib.sessions import find_orphan_transcripts

    proj = fake_repos / "myproj"
    proj.mkdir()
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    _write_jsonl(claude_projects, encoded, "uuid-ccc", "20260504-my-orphan")

    results = find_orphan_transcripts("my-orphan", roots=[fake_repos])
    assert len(results) == 1
    assert results[0].basename == "20260504-my-orphan"
    assert results[0].is_orphan is True
