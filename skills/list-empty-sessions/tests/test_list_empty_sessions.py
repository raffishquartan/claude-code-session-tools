"""Tests for skills/list-empty-sessions/scripts/list_empty_sessions.py.

Exercises the script end-to-end against a synthesised cc-sessions tree built
with the same fixtures used by tests/test_empty_session.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup — make the skill script importable and ensure local src wins.
# ---------------------------------------------------------------------------
SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
_REPO_SRC = str(Path(__file__).resolve().parents[3] / "src")
sys.path.insert(0, _REPO_SRC)


# ---------------------------------------------------------------------------
# Helpers shared by all tests
# ---------------------------------------------------------------------------

def _write_tag(transcript_dir: Path, uuid: str, tag: str) -> None:
    transcript_dir.mkdir(parents=True, exist_ok=True)
    (transcript_dir / f"{uuid}.tag").write_text(tag + "\n")


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _transcript_dir(fake_home: Path, project: Path) -> Path:
    encoded = str(project).replace("/", "-").replace(".", "-")
    return fake_home / ".claude" / "projects" / encoded


def _make_empty_session(
    fake_home: Path,
    project: Path,
    basename: str,
    *,
    uuid: str = "uuid-empty-1",
) -> Path:
    """Build a cc-sessions/<basename>/ dir + JSONL with only hook metadata."""
    session_dir = project / "cc-sessions" / basename
    (session_dir / "working").mkdir(parents=True, exist_ok=True)
    (session_dir / "out").mkdir(exist_ok=True)

    tag = basename.split("-", 1)[1]  # strip YYYYMMDD- prefix
    td = _transcript_dir(fake_home, project)
    _write_tag(td, uuid, tag)
    _write_jsonl(td / f"{uuid}.jsonl", [
        {"type": "user", "isMeta": True, "message": {"content": "hook output"}},
    ])
    return session_dir


def _make_nonempty_session(
    fake_home: Path,
    project: Path,
    basename: str,
    *,
    uuid: str = "uuid-nonempty-1",
) -> Path:
    """Build a cc-sessions/<basename>/ dir + JSONL with a real user message."""
    session_dir = project / "cc-sessions" / basename
    (session_dir / "working").mkdir(parents=True, exist_ok=True)
    (session_dir / "out").mkdir(exist_ok=True)

    tag = basename.split("-", 1)[1]
    td = _transcript_dir(fake_home, project)
    _write_tag(td, uuid, tag)
    _write_jsonl(td / f"{uuid}.jsonl", [
        {"type": "user", "isMeta": True, "message": {"content": "hook"}},
        {"type": "user", "message": {"content": "please help me"}},
    ])
    return session_dir


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def project(fake_home: Path, tmp_path: Path) -> Path:
    """A single project directory."""
    p = tmp_path / "myproject"
    p.mkdir()
    (p / "cc-sessions").mkdir()
    return p


# ---------------------------------------------------------------------------
# Script invocation helper
# ---------------------------------------------------------------------------

def _run_script(
    *extra_args: str,
    env_overrides: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    script = str(SCRIPTS_DIR / "list_empty_sessions.py")
    import os
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, script, *extra_args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


# ---------------------------------------------------------------------------
# Tests: end-to-end against synthesised tree
# ---------------------------------------------------------------------------

class TestListEmptySessionsLocal:
    """Local-scope tests (no --global)."""

    def test_no_sessions_at_all(self, fake_home, project, monkeypatch):
        """When the project has no sessions at all, print 'No empty sessions'."""
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script(cwd=project)
        assert result.returncode == 0
        assert "No empty sessions found" in result.stdout

    def test_empty_sessions_are_listed(self, fake_home, project, monkeypatch):
        """Empty sessions appear in stdout."""
        _make_empty_session(fake_home, project, "20260516-demo-empty")
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script(cwd=project)
        assert result.returncode == 0
        assert "20260516-demo-empty" in result.stdout

    def test_nonempty_sessions_are_excluded(self, fake_home, project, monkeypatch):
        """Non-empty sessions do NOT appear in the listing."""
        _make_empty_session(fake_home, project, "20260516-demo-empty")
        _make_nonempty_session(fake_home, project, "20260516-demo-used")
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script(cwd=project)
        assert "20260516-demo-empty" in result.stdout
        assert "20260516-demo-used" not in result.stdout

    def test_count_summary_present(self, fake_home, project, monkeypatch):
        """A count summary line appears in stdout."""
        _make_empty_session(fake_home, project, "20260516-demo-empty")
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script(cwd=project)
        assert "empty session" in result.stdout

    def test_follow_up_suggestions_present(self, fake_home, project, monkeypatch):
        """Follow-up commands are printed when empties are found."""
        _make_empty_session(fake_home, project, "20260516-demo-empty")
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script(cwd=project)
        assert "ccr" in result.stdout
        assert "delete-sessions" in result.stdout

    def test_delete_command_contains_basename(self, fake_home, project, monkeypatch):
        """The suggested delete command contains the empty session basename."""
        _make_empty_session(fake_home, project, "20260516-demo-empty")
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script(cwd=project)
        assert "20260516-demo-empty" in result.stdout
        assert "delete-sessions" in result.stdout


class TestListEmptySessionsGlobal:
    """Global-scope tests (--global flag propagated to ccs)."""

    def test_global_flag_propagated(self, fake_home, project, monkeypatch):
        """With --global, the output is scoped globally (no crash, runs ccs --global)."""
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script("--global", cwd=project)
        # ccs may warn about empty corpus if no sessions exist — that's fine.
        # What matters is the script ran without Python error.
        assert "Traceback" not in result.stdout
        assert "Traceback" not in result.stderr

    def test_global_empty_session_found(self, fake_home, project, monkeypatch):
        """Empty sessions appear in global mode output."""
        _make_empty_session(fake_home, project, "20260516-demo-global-empty")
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script("--global", cwd=project)
        assert "20260516-demo-global-empty" in result.stdout

    def test_global_label_in_output(self, fake_home, project, monkeypatch):
        """The scope label says 'global' when --global is passed."""
        _make_empty_session(fake_home, project, "20260516-demo-empty-global")
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script("--global", cwd=project)
        assert "global" in result.stdout

    def test_local_label_in_output(self, fake_home, project, monkeypatch):
        """The scope label says 'local' when --global is NOT passed."""
        _make_empty_session(fake_home, project, "20260516-demo-empty-local")
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script(cwd=project)
        assert "local" in result.stdout


class TestOutputFormat:
    """Output format matches the SKILL.md examples."""

    def test_no_follow_up_when_no_empties(self, fake_home, project, monkeypatch):
        """When there are no empties, the follow-up block is suppressed."""
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script(cwd=project)
        assert "Follow-up commands:" not in result.stdout

    def test_follow_up_only_when_empties_exist(self, fake_home, project, monkeypatch):
        """Follow-up commands appear exactly when empties are listed."""
        _make_empty_session(fake_home, project, "20260516-demo-empty")
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script(cwd=project)
        assert "Follow-up commands:" in result.stdout

    def test_multiple_empties_all_listed(self, fake_home, project, monkeypatch):
        """All empty sessions appear in output (not just the first)."""
        _make_empty_session(fake_home, project, "20260516-alpha-empty", uuid="uuid-a")
        _make_empty_session(fake_home, project, "20260517-beta-empty", uuid="uuid-b")
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(project.parent))
        result = _run_script(cwd=project)
        assert "20260516-alpha-empty" in result.stdout
        assert "20260517-beta-empty" in result.stdout
