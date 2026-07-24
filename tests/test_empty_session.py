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
    """Synthesise a project with a cc-sessions directory, a transcript dir,
    and a sessions.db under a fake HOME so the encoded path matches what the
    helpers expect."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    # sessions.db location — controlled via env var so sessions.py picks it up.
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(db_dir))

    project = fake_home / "repos" / "demo"
    project.mkdir(parents=True)
    (project / "cc-sessions").mkdir()

    return fake_home, project, db_dir


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")


def _write_tag(tags_dir: Path, uuid: str, tag: str) -> None:
    """Record uuid -> tag in sessions.db (the `tags_dir` parameter name is
    kept for call-site compatibility across this file's many pre-existing
    tests; it now names the sessions.db *directory*, matching what the
    synthetic_project fixture returns)."""
    from cc_session_tools.lib import sessions_db
    sessions_db.write_tag(uuid, tag, path=tags_dir / "sessions.db")


# ---------- find_jsonl_for_session ----------


def test_find_jsonl_via_tag_file(synthetic_project):
    _, project, tags_dir = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(tags_dir, "uuid-1", "demo-feature")
    _write_jsonl(transcript_dir / "uuid-1.jsonl", [{"type": "summary"}])

    found = find_jsonl_for_session(basename, project)
    assert found == transcript_dir / "uuid-1.jsonl"


def test_find_jsonl_returns_none_when_no_transcript_dir(synthetic_project):
    _, project, tags_dir = synthetic_project
    assert find_jsonl_for_session("20260516-missing", project) is None


def test_find_jsonl_returns_none_when_no_tag_match(synthetic_project):
    _, project, tags_dir = synthetic_project
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(tags_dir, "uuid-x", "different-tag")
    _write_jsonl(transcript_dir / "uuid-x.jsonl", [])

    assert find_jsonl_for_session("20260516-demo-feature", project) is None


# ---------- is_empty_session ----------


def test_empty_when_only_hook_metadata(synthetic_project):
    _, project, tags_dir = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(tags_dir, "u1", "demo-feature")
    _write_jsonl(transcript_dir / "u1.jsonl", [
        {"type": "user", "isMeta": True, "message": {"content": "hook output"}},
        {"type": "user", "message": {"content": "<system-reminder>x"}},
        {"type": "summary"},
    ])

    assert is_empty_session(basename, project) is True


def test_non_empty_with_real_user_message(synthetic_project):
    _, project, tags_dir = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(tags_dir, "u1", "demo-feature")
    _write_jsonl(transcript_dir / "u1.jsonl", [
        {"type": "user", "isMeta": True, "message": {"content": "hook"}},
        {"type": "user", "message": {"content": "please help me with X"}},
    ])

    assert is_empty_session(basename, project) is False


def test_non_empty_with_list_content_text_block(synthetic_project):
    _, project, tags_dir = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(tags_dir, "u1", "demo-feature")
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
    _, project, tags_dir = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(tags_dir, "u1", "demo-feature")
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
    _, project, tags_dir = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(tags_dir, "u1", "demo-feature")
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
    _, project, tags_dir = synthetic_project
    assert session_is_empty_safe("20260516-missing", project) is None


def test_safe_returns_bool_when_jsonl_found(synthetic_project):
    _, project, tags_dir = synthetic_project
    basename = "20260516-demo-feature"
    transcript_dir = transcript_dir_for_project(project)
    _write_tag(tags_dir, "u1", "demo-feature")
    _write_jsonl(transcript_dir / "u1.jsonl", [
        {"type": "user", "isMeta": True, "message": {"content": "hook"}},
    ])

    assert session_is_empty_safe(basename, project) is True


# ---------- find_jsonl_for_session: custom-title preferred over lone tag-file ----------

def test_find_jsonl_prefers_custom_title_over_unconfirmed_tag_file(synthetic_project):
    """Defence-in-depth: when a .tag file match has no custom-title record but
    another JSONL does have a matching custom-title, prefer the custom-title match.

    This guards against hook sub-process transcripts that inherit the parent
    session tag and write a .tag file that would otherwise steal the lookup.
    """
    _, project, tags_dir = synthetic_project
    basename = "20260516-real-session"
    transcript_dir = transcript_dir_for_project(project)

    # hook-stub.jsonl: has a .tag file pointing to the session tag, but no
    # custom-title record (simulates a hook sub-process transcript that
    # inherited the parent's CLD_SESSION_TAG).
    _write_tag(tags_dir, "uuid-hook-stub", "real-session")
    _write_jsonl(transcript_dir / "uuid-hook-stub.jsonl", [
        {"type": "user", "isMeta": True, "message": {"content": "hook output"}},
    ])

    # uuid-real.jsonl: no .tag file, but has the canonical custom-title record
    # using the real field name CC writes: "customTitle" (camelCase).
    _write_jsonl(transcript_dir / "uuid-real.jsonl", [
        {"type": "custom-title", "customTitle": "20260516-real-session", "sessionId": "uuid-real"},
        {"type": "user", "message": {"content": "the real work"}},
    ])

    found = find_jsonl_for_session(basename, project)

    assert found == transcript_dir / "uuid-real.jsonl", (
        f"expected uuid-real.jsonl but got {found}"
    )


def test_find_jsonl_hook_with_tag_and_real_session_with_tag_and_custom_title(synthetic_project):
    """Root-cause regression test: a hook transcript that inherited the parent
    session tag must not be returned when the real session JSONL has both a
    tag file AND a customTitle record.

    Before the fix, _jsonl_has_custom_title looked for rec["title"] instead of
    rec["customTitle"], so the real session was never confirmed and the hook
    transcript (tag file only, first in glob order) was returned instead.
    """
    _, project, tags_dir = synthetic_project
    basename = "20260531-real-session"
    suffix = "real-session"
    transcript_dir = transcript_dir_for_project(project)

    # Hook session: has a tag file (inherited from parent), no custom-title.
    _write_tag(tags_dir, "aaa-hook-inherited", suffix)
    _write_jsonl(transcript_dir / "aaa-hook-inherited.jsonl", [
        {"type": "user", "isMeta": True, "message": {"content": "security hook output"}},
    ])

    # Real session: has both a tag file AND a customTitle record.
    _write_tag(tags_dir, "zzz-real-session", suffix)
    _write_jsonl(transcript_dir / "zzz-real-session.jsonl", [
        {"type": "custom-title", "customTitle": basename, "sessionId": "zzz-real-session"},
        {"type": "user", "message": {"content": "actual user work"}},
    ])

    found = find_jsonl_for_session(basename, project)

    assert found == transcript_dir / "zzz-real-session.jsonl", (
        f"expected zzz-real-session.jsonl but got {found}"
    )
