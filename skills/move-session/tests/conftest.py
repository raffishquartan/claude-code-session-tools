"""Shared pytest fixtures for move-session tests.

Loads `move_session` as a module from the sibling scripts/ dir so tests can
exercise its helpers directly. Provides fixtures that build a synthetic
HOME + cc-sessions tree under tmp_path, so tests never touch the real
~/.claude state.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
# Always prepend this repo's src first so in-process tests use the local
# cc_session_tools. The unconditional insert (not guarded by `not in`) is
# intentional: a previously installed editable package may already be on
# sys.path, but it could point at an older source tree. Putting the worktree
# src at index 0 ensures the correct version wins regardless.
_REPO_SRC = str(Path(__file__).resolve().parents[3] / "src")
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, _REPO_SRC)

import move_session  # noqa: E402  (sys.path mutation must come first)


@pytest.fixture
def ms():
    """Convenience handle to the loaded move_session module."""
    return move_session


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set HOME to a fresh tmp dir and return it. The script reads
    `Path.home() / '.claude' / 'projects'` to find session JSONLs, so
    isolating HOME isolates that lookup from the real config. Also clears
    CLAUDE_SESSION_TOOLS_*_ROOT env vars so individual tests can opt into
    specific roots without inheriting the developer's shell config.

    Also sets CCCS_SESSION_TAGS_DIR to a flat tags dir inside tmp_path so
    tag file reads/writes go to an isolated location (not ~/.cache/claude/).
    The env var is propagated to subprocesses via the `env=` dict in _run().
    """
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
    # Flat tags dir for test isolation.
    tags_dir = tmp_path / "session-tags"
    tags_dir.mkdir()
    monkeypatch.setenv("CCCS_SESSION_TAGS_DIR", str(tags_dir))
    return home


@pytest.fixture
def roots_file(tmp_home: Path, tmp_path: Path,
               monkeypatch: pytest.MonkeyPatch) -> Path:
    """Configure `tmp_path/projects-root` as the only valid (loose) session
    root via CLAUDE_SESSION_TOOLS_REPO_ROOT, and create the directory itself.

    Returns the projects-root path. Despite the legacy fixture name, no
    cc-session-roots.txt file is written - roots discovery is now env-var
    driven (see cc_session_tools.lib.roots)."""
    root = tmp_path / "projects-root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(root))
    return root


@pytest.fixture
def projects_root(tmp_path: Path) -> Path:
    """Returns the parent dir under which valid project cwds may sit
    (matches the line in `roots_file`)."""
    return tmp_path / "projects-root"


@pytest.fixture
def make_session(tmp_home: Path, projects_root: Path):
    """Factory for building a synthetic source session under a given project
    cwd. Returns (cc_sessions_dir, jsonl_path, uuid).

    Defaults: 2-record jsonl with valid CC-style records so tombstone path works.
    """
    def _make(project_name: str, tag: str, *, uuid: str | None = None,
              extra_jsonls: int = 0) -> tuple[Path, Path, str]:
        import uuid as uuidlib
        if uuid is None:
            uuid = str(uuidlib.uuid4())

        cwd = projects_root / project_name
        cwd.mkdir(parents=True, exist_ok=True)
        session_dir = cwd / "cc-sessions" / tag
        (session_dir / "working").mkdir(parents=True)
        (session_dir / "out").mkdir()
        (session_dir / "working" / "WORKLOG.md").write_text("test worklog\n")

        encoded = str(cwd).replace("/", "-")
        key_dir = tmp_home / ".claude" / "projects" / encoded
        key_dir.mkdir(parents=True, exist_ok=True)
        jsonl = key_dir / f"{uuid}.jsonl"

        records = [
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "timestamp": "2026-05-03T13:00:00.000Z",
                "cwd": str(cwd),
                "sessionId": uuid,
                "version": "2.1.126",
                "gitBranch": "",
                "message": {"role": "user", "content": f"hello from {cwd}"},
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "parentUuid": "u1",
                "timestamp": "2026-05-03T13:00:01.000Z",
                "cwd": str(cwd),
                "sessionId": uuid,
                "version": "2.1.126",
                "gitBranch": "",
                "message": {
                    "model": "test",
                    "id": "msg_a1",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ack"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "stop_details": None,
                    "usage": {
                        "input_tokens": 1, "output_tokens": 1,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                    "diagnostics": {},
                },
            },
        ]
        jsonl.write_text("".join(json.dumps(r) + "\n" for r in records))

        for i in range(extra_jsonls):
            extra_uuid = str(uuidlib.uuid4())
            extra_path = key_dir / f"{extra_uuid}.jsonl"
            extra_path.write_text(json.dumps({
                "type": "user",
                "uuid": "x1",
                "timestamp": "2026-05-03T11:00:00.000Z",
                "cwd": str(cwd),
                "sessionId": extra_uuid,
                "message": {"role": "user", "content": f"sibling session {i}"},
            }) + "\n")

        return session_dir, jsonl, uuid
    return _make
