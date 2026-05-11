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


# ---------------------------------------------------------------------------
# Test 3: On-disk match wins - no duplicate when cc-sessions dir exists
# ---------------------------------------------------------------------------

def test_no_duplicate_when_on_disk_match_exists(fake_repos, claude_projects, monkeypatch, capsys):
    """When cc-sessions dir exists, --include-orphans must not produce a duplicate match."""
    proj = fake_repos / "myproj"
    _make_session(fake_repos, "myproj", "20260504-found-session")
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    # Also write a JSONL for the same session basename
    _write_jsonl(claude_projects, encoded, "uuid-ddd", "20260504-found-session")

    # Verify find_matching_sessions + find_orphan_transcripts combined yield one result
    from cc_session_tools.lib.sessions import find_matching_sessions, find_orphan_transcripts
    on_disk = find_matching_sessions("found-session", roots=[fake_repos])
    orphans = find_orphan_transcripts("found-session", roots=[fake_repos])
    on_disk_basenames = {m.basename for m in on_disk}
    combined = on_disk + [o for o in orphans if o.basename not in on_disk_basenames]
    assert len(combined) == 1
    assert combined[0].is_orphan is False  # on-disk match wins


def test_find_orphan_transcripts_skips_when_session_dir_exists(fake_repos, claude_projects):
    """find_orphan_transcripts must NOT return a match if cc-sessions/<name>/ exists."""
    from cc_session_tools.lib.sessions import find_orphan_transcripts

    # Create the on-disk session directory
    _make_session(fake_repos, "myproj", "20260504-has-dir")
    proj = fake_repos / "myproj"
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    _write_jsonl(claude_projects, encoded, "uuid-eee", "20260504-has-dir")

    results = find_orphan_transcripts("has-dir", roots=[fake_repos])
    assert results == []


# ---------------------------------------------------------------------------
# Test 4: Printed/picker label shows [orphan] for orphan entries
# ---------------------------------------------------------------------------

def test_multi_match_list_shows_orphan_prefix(fake_repos, claude_projects, capsys, monkeypatch):
    """When multiple matches include an orphan, the printed list shows [orphan]."""
    # One on-disk session
    _make_session(fake_repos, "myproj", "20260504-foo-onedisk")
    # One orphan session
    proj = fake_repos / "myproj"
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    _write_jsonl(claude_projects, encoded, "uuid-fff", "20260503-foo-orphan")

    # Force isatty to False so we get the printed list (not picker)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    rc = ccr.main(["--include-orphans", "foo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[orphan]" in out
    assert "20260503-foo-orphan" in out


# ---------------------------------------------------------------------------
# Test 5: Unresolvable orphan (no name-cache entry) is silently skipped
# ---------------------------------------------------------------------------

def test_unresolvable_orphan_silently_skipped(fake_repos, claude_projects):
    """A JSONL transcript with no custom-title record must be silently skipped."""
    from cc_session_tools.lib.sessions import find_orphan_transcripts

    proj = fake_repos / "myproj"
    proj.mkdir()
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    # Write a JSONL with no custom-title record (unresolvable)
    proj_transcript_dir = claude_projects / encoded
    proj_transcript_dir.mkdir(parents=True, exist_ok=True)
    jsonl = proj_transcript_dir / "uuid-hhh.jsonl"
    jsonl.write_text('{"type": "assistant", "sessionId": "uuid-hhh"}\n')

    results = find_orphan_transcripts("anything", roots=[fake_repos])
    assert results == []


def test_unresolvable_orphan_not_shown_in_cli(fake_repos, claude_projects, capsys, captured_launch):
    """CLI with --include-orphans must not list unresolvable transcripts."""
    proj = fake_repos / "myproj"
    proj.mkdir()
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    proj_transcript_dir = claude_projects / encoded
    proj_transcript_dir.mkdir(parents=True, exist_ok=True)
    (proj_transcript_dir / "uuid-iii.jsonl").write_text(
        '{"type": "assistant", "sessionId": "uuid-iii"}\n'
    )

    rc = ccr.main(["--include-orphans", "anything"])
    assert rc == 1  # no match
    assert "cmd" not in captured_launch


def test_picker_label_shows_orphan_prefix(fake_repos, claude_projects, monkeypatch, captured_launch):
    """In picker mode, orphan entries are prefixed with [orphan]."""
    # Two sessions so picker is shown
    _make_session(fake_repos, "myproj", "20260504-foo-onedisk")
    proj = fake_repos / "myproj"
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    _write_jsonl(claude_projects, encoded, "uuid-ggg", "20260503-foo-orphan")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    captured_labels: list[list[str]] = []

    from cc_session_tools.lib import picker
    def fake_pick(labels):
        captured_labels.append(labels)
        return 0  # pick first

    monkeypatch.setattr(picker, "pick_from_list", fake_pick)

    ccr.main(["--include-orphans", "foo"])
    assert len(captured_labels) == 1
    assert any("[orphan]" in label for label in captured_labels[0])


# ---------------------------------------------------------------------------
# Test 6: Selecting an orphan in the picker execs claude --resume <basename>
# ---------------------------------------------------------------------------

def test_selecting_orphan_in_picker_resumes_by_basename(
    fake_repos, claude_projects, monkeypatch, captured_launch
):
    """Selecting an orphan from the picker must exec claude --resume <basename>."""
    # Create one orphan and one on-disk session so picker is shown
    _make_session(fake_repos, "myproj", "20260504-foo-onedisk")
    proj = fake_repos / "myproj"
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    _write_jsonl(claude_projects, encoded, "uuid-jjj", "20260503-foo-orphan")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    from cc_session_tools.lib import picker
    # Select the orphan: sort is by date descending, so 20260504 comes first (idx 0),
    # 20260503 (orphan) is idx 1.
    monkeypatch.setattr(picker, "pick_from_list", lambda _: 1)

    rc = ccr.main(["--include-orphans", "foo"])
    assert rc == 0
    assert "cmd" in captured_launch
    cmd = captured_launch["cmd"]
    assert cmd[0] == "claude"
    assert "--resume" in cmd
    assert "20260503-foo-orphan" in cmd


def test_selecting_orphan_prints_warning_to_stderr(
    fake_repos, claude_projects, monkeypatch, captured_launch, capsys
):
    """Selecting an orphan must emit a one-line stderr warning about missing dir."""
    proj = fake_repos / "myproj"
    proj.mkdir()
    encoded = str(proj.resolve()).replace("/", "-").replace(".", "-")
    _write_jsonl(claude_projects, encoded, "uuid-kkk", "20260503-foo-orphan")

    # Only one match -> direct resume (no picker)
    rc = ccr.main(["--include-orphans", "foo-orphan"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "orphan" in err.lower() or "no on-disk" in err.lower()
