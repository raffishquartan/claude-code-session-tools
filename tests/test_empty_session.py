"""Tests for is_empty_session / find_jsonl_for_session in lib.sessions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_session_tools.lib.sessions import (
    find_jsonl_for_session,
    is_empty_session,
    session_is_empty_safe,
    transcript_dir_for_project,
)


@pytest.fixture
def synthetic_project(tmp_path, monkeypatch):
    """Synthesise a project with a cc-sessions directory and a transcript dir
    under a fake HOME so the encoded path matches what the helpers expect."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    project = fake_home / "repos" / "demo"
    project.mkdir(parents=True)
    (project / "cc-sessions").mkdir()

    return fake_home, project


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _write_tag(transcript_dir: Path, uuid: str, tag: str) -> None:
    transcript_dir.mkdir(parents=True, exist_ok=True)
    (transcript_dir / f"{uuid}.tag").write_text(tag + "\n")


# ---------- find_jsonl_for_session ----------


def test_find_jsonl_via_tag_file(synthetic_project):
    _, project = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(transcript_dir, "uuid-1", "demo-feature")
    _write_jsonl(transcript_dir / "uuid-1.jsonl", [{"type": "summary"}])

    found = find_jsonl_for_session(basename, project)
    assert found == transcript_dir / "uuid-1.jsonl"


def test_find_jsonl_returns_none_when_no_transcript_dir(synthetic_project):
    _, project = synthetic_project
    assert find_jsonl_for_session("20260516-missing", project) is None


def test_find_jsonl_returns_none_when_no_tag_match(synthetic_project):
    _, project = synthetic_project
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(transcript_dir, "uuid-x", "different-tag")
    _write_jsonl(transcript_dir / "uuid-x.jsonl", [])

    assert find_jsonl_for_session("20260516-demo-feature", project) is None


# ---------- is_empty_session ----------


def test_empty_when_only_hook_metadata(synthetic_project):
    _, project = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(transcript_dir, "u1", "demo-feature")
    _write_jsonl(transcript_dir / "u1.jsonl", [
        {"type": "user", "isMeta": True, "message": {"content": "hook output"}},
        {"type": "user", "message": {"content": "<system-reminder>x"}},
        {"type": "summary"},
    ])

    assert is_empty_session(basename, project) is True


def test_non_empty_with_real_user_message(synthetic_project):
    _, project = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(transcript_dir, "u1", "demo-feature")
    _write_jsonl(transcript_dir / "u1.jsonl", [
        {"type": "user", "isMeta": True, "message": {"content": "hook"}},
        {"type": "user", "message": {"content": "please help me with X"}},
    ])

    assert is_empty_session(basename, project) is False


def test_non_empty_with_list_content_text_block(synthetic_project):
    _, project = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(transcript_dir, "u1", "demo-feature")
    _write_jsonl(transcript_dir / "u1.jsonl", [
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "content": "ok"},
                    {"type": "text", "text": "here is what I want"},
                ]
            },
        },
    ])

    assert is_empty_session(basename, project) is False


def test_empty_when_list_content_is_only_tool_results(synthetic_project):
    _, project = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(transcript_dir, "u1", "demo-feature")
    _write_jsonl(transcript_dir / "u1.jsonl", [
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "content": "ok"},
                ]
            },
        },
    ])

    assert is_empty_session(basename, project) is True


def test_empty_when_text_block_is_slash_command(synthetic_project):
    _, project = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(transcript_dir, "u1", "demo-feature")
    _write_jsonl(transcript_dir / "u1.jsonl", [
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "<command-name>/help</command-name>"},
                ]
            },
        },
    ])

    assert is_empty_session(basename, project) is True


def test_safe_returns_none_when_jsonl_missing(synthetic_project):
    _, project = synthetic_project
    assert session_is_empty_safe("20260516-missing", project) is None


def test_safe_returns_bool_when_jsonl_found(synthetic_project):
    _, project = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(transcript_dir, "u1", "demo-feature")
    _write_jsonl(transcript_dir / "u1.jsonl", [
        {"type": "user", "isMeta": True, "message": {"content": "hook"}},
    ])

    assert session_is_empty_safe(basename, project) is True
