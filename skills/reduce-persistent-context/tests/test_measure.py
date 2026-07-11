from pathlib import Path

import pytest

import measure

FIX = Path(__file__).parent / "fixtures"


def test_reads_claude_md():
    recs = measure.measure_claude_md(FIX / "claude_home")
    names = {r.name for r in recs}
    assert "CLAUDE.md (global)" in names
    assert "specifics/extra.md (claude_md_global)" in names  # @import resolved
    assert all(r.category == "claude_md_global" for r in recs)
    assert all(r.tokens > 0 for r in recs)


def test_reads_project_claude_md():
    recs = measure.measure_project_claude_md(FIX / "project_root")
    names = {r.name for r in recs}
    assert "CLAUDE.md (project)" in names
    assert "specifics/project-extra.md (claude_md_project)" in names  # @import resolved
    assert all(r.category == "claude_md_project" for r in recs)
    assert all(r.tokens > 0 for r in recs)


def test_project_claude_md_absent_returns_empty(tmp_path):
    # No CLAUDE.md in tmp_path -> no error, matching the global-file-absent case.
    assert measure.measure_project_claude_md(tmp_path) == []


def test_global_and_project_claude_md_rows_are_distinguishable():
    global_recs = measure.measure_claude_md(FIX / "claude_home")
    project_recs = measure.measure_project_claude_md(FIX / "project_root")
    global_names = {r.name for r in global_recs}
    project_names = {r.name for r in project_recs}
    assert global_names.isdisjoint(project_names)
    global_categories = {r.category for r in global_recs}
    project_categories = {r.category for r in project_recs}
    assert global_categories.isdisjoint(project_categories)


def test_reads_skill_descriptions():
    recs = measure.measure_skill_descriptions(FIX / "claude_home" / "skills")
    assert any(r.name == "foo" and r.category == "skill_desc" for r in recs)


def test_captured_missing_section_raises(tmp_path):
    (tmp_path / "captured-context.txt").write_text("### DEFERRED_TOOL_NAMES\nx\n")
    with pytest.raises(ValueError, match="SESSIONSTART_HOOKS"):
        measure.measure_captured(tmp_path / "captured-context.txt")


def test_captured_all_sections_ok():
    recs = measure.measure_captured(FIX / "captured-context.txt")
    cats = {r.category for r in recs}
    assert cats == {"mcp_names", "deferred_tools", "hooks", "mcp_instructions"}


def test_deferred_tools_split_per_server():
    section = (
        "WebFetch\n"
        "mcp__opentabs__tesco_search_products\n"
        "mcp__opentabs__tesco_add_to_basket\n"
        "mcp__amazon-shopping__amazon_search\n"
    )
    recs = measure.parse_deferred_tools(section)
    by_name = {r.name: r for r in recs}
    assert by_name["opentabs"].category == "mcp_names"
    assert by_name["amazon-shopping"].category == "mcp_names"
    assert by_name["builtin-deferred"].category == "deferred_tools"
    # opentabs has two tools, amazon-shopping one -> opentabs costs more
    assert by_name["opentabs"].tokens > by_name["amazon-shopping"].tokens


def test_harness_is_estimated():
    rec = measure.measure_harness_baseline()
    assert rec.category == "harness"
    assert "(estimated)" in rec.detail
