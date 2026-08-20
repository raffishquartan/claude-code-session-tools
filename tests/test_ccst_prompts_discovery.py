from __future__ import annotations

from cc_session_tools.cli.ccst import _discover_prompts_dir


def test_discover_prompts_dir_finds_bundled_prompts():
    d = _discover_prompts_dir()
    assert d.is_dir()
    assert (d / "pdata-migration-claude-md-update.md").is_file()
    assert (d / "pdata-migration-skills-update.md").is_file()
