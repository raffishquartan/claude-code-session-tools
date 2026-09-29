"""Tests for the pdata post-migration skills (openspec/changes/pdata-post-migration-skills).

Both skills are pure instruction skills with no backing script, so what is mechanically testable
is discovery, frontmatter, and that each behavior the spec requires is actually written down.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.cli import ccst

DOCS_SKILL = "pm-pdata-do-update-project-docs"
CONSUMING_SKILL = "pm-pdata-do-update-consuming-skills"


def _skills() -> dict[str, Path]:
    return {p.name: p for p in ccst._discover_skills(ccst._discover_source_dir())}


def _text(name: str) -> str:
    return (_skills()[name] / "SKILL.md").read_text()


@pytest.mark.parametrize("name", [DOCS_SKILL, CONSUMING_SKILL])
def test_skill_is_bundled_with_matching_frontmatter(name):
    assert name in _skills()
    text = _text(name)
    assert text.startswith(f"---\nname: {name}\n")
    assert "\ndescription: " in text.split("\n---\n", 1)[0]


@pytest.mark.parametrize("name", [DOCS_SKILL, CONSUMING_SKILL])
def test_skill_requires_fresh_context_subagent(name):
    text = _text(name)
    assert "fresh context" in text
    assert "subagent" in text
    assert "not inline" in text


@pytest.mark.parametrize("name", [DOCS_SKILL, CONSUMING_SKILL])
def test_skill_aborts_without_project_claude_md(name):
    text = _text(name)
    assert "ls CLAUDE.md" in text
    assert "Aborting without touching" in text


@pytest.mark.parametrize("name", [DOCS_SKILL, CONSUMING_SKILL])
def test_skill_has_no_claude_p_invocation_or_prompt_file_reference(name):
    text = _text(name)
    assert "claude -p" not in text
    assert "pdata-migration-" not in text


def test_docs_skill_cross_checks_before_treating_a_reference_as_stale():
    text = _text(DOCS_SKILL)
    assert "ccst pdata list" in text
    assert ".pdata-migrated/" in text
    assert "folder-owned" in text


def test_docs_skill_names_record_group_and_ccst_pdata_commands_in_rewrites():
    text = _text(DOCS_SKILL)
    for command in ("ccst pdata list", "ccst pdata get", "ccst pdata query"):
        assert command in text
    assert "record_group" in text


def test_docs_skill_is_idempotent():
    text = _text(DOCS_SKILL)
    assert "Idempotency" in text
    assert "leave it alone" in text


def test_docs_skill_reports_layout_drift_without_acting_on_it():
    text = _text(DOCS_SKILL)
    assert "pm-project-layout-reference" in text
    assert "500" in text
    assert "ws-XX-<slug>" in text
    assert "Do not restructure" in text
    assert "ccst pdata reorganize" in text


def test_consuming_skill_matches_literal_paths_only():
    text = _text(CONSUMING_SKILL)
    assert "~/.claude/skills/" in text
    assert "literal path" in text
    assert ".pdata-migrated/" in text
    assert "Do not widen" in text


def test_consuming_skill_classifies_direct_vs_passing_mentions():
    text = _text(CONSUMING_SKILL)
    assert "Direct read/write" in text
    assert "Passing mention" in text


def test_consuming_skill_maps_to_each_ccst_pdata_verb_and_schema_show():
    text = _text(CONSUMING_SKILL)
    for command in (
        "ccst pdata list",
        "ccst pdata get",
        "ccst pdata query",
        "ccst pdata add",
        "ccst pdata update",
        "ccst pdata schema show",
    ):
        assert command in text


def test_consuming_skill_flags_unsafe_rewrites_and_reports_every_match():
    text = _text(CONSUMING_SKILL)
    for phrase in ("locking", "mtime", "row order", "needs a human decision", "left alone"):
        assert phrase in text
    assert "every match" in text


def test_pm_pdata_do_init_runs_both_skills_after_write_docs_first():
    text = _text("pm-pdata-do-init")
    assert DOCS_SKILL in text
    assert CONSUMING_SKILL in text
    assert text.index(DOCS_SKILL) < text.index(CONSUMING_SKILL)
