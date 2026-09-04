"""Tests for ccst.py's `_discover_source_dir`/`_discover_bundle`/`_discover_prompts_dir` —
must resolve the bundled skills/, config/, and prompts/ correctly whether cc_session_tools is
running from an editable source checkout or an installed wheel, since all three layouts place
cc_session_tools/skills/, cc_session_tools/config/, and cc_session_tools/prompts/ at the same
relative depth from this module (src/cc_session_tools/cli/ccst.py -> ../skills, ../config,
../prompts)."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.cli import ccst


def test_discover_source_dir_finds_the_real_bundled_skills_dir():
    result = ccst._discover_source_dir()
    assert result.is_dir()
    assert result.name == "skills"
    # Sits directly inside the cc_session_tools package, not at the repo root -
    # this is what makes it resolve the same way in an installed wheel.
    assert result.parent.name == "cc_session_tools"
    assert (result / "delete-sessions" / "SKILL.md").is_file()


def test_discover_bundle_finds_the_real_bundled_hooks_bundle_json():
    result = ccst._discover_bundle()
    assert result.is_file()
    assert result.name == "hooks-bundle.json"
    assert result.parent.name == "config"
    assert result.parent.parent.name == "cc_session_tools"


def test_discover_source_dir_raises_clearly_when_package_relocated_away(monkeypatch, tmp_path):
    """Simulate a broken install (module file present, no sibling skills/ dir) -
    must fail loudly, not silently return an unrelated directory."""
    fake_module_file = tmp_path / "cc_session_tools" / "cli" / "ccst.py"
    fake_module_file.parent.mkdir(parents=True)
    fake_module_file.touch()
    monkeypatch.setattr(ccst, "__file__", str(fake_module_file))

    with pytest.raises(FileNotFoundError, match="Cannot locate bundled skills"):
        ccst._discover_source_dir()


def test_discover_bundle_raises_clearly_when_package_relocated_away(monkeypatch, tmp_path):
    fake_module_file = tmp_path / "cc_session_tools" / "cli" / "ccst.py"
    fake_module_file.parent.mkdir(parents=True)
    fake_module_file.touch()
    monkeypatch.setattr(ccst, "__file__", str(fake_module_file))

    with pytest.raises(FileNotFoundError, match="Cannot locate bundled config/hooks-bundle.json"):
        ccst._discover_bundle()


def test_discover_prompts_dir_finds_the_real_bundled_prompts():
    result = ccst._discover_prompts_dir()
    assert result.is_dir()
    assert result.name == "prompts"
    assert result.parent.name == "cc_session_tools"
    assert (result / "pdata-migration-claude-md-update.md").is_file()
    assert (result / "pdata-migration-skills-update.md").is_file()


def test_discover_prompts_dir_raises_clearly_when_package_relocated_away(monkeypatch, tmp_path):
    fake_module_file = tmp_path / "cc_session_tools" / "cli" / "ccst.py"
    fake_module_file.parent.mkdir(parents=True)
    fake_module_file.touch()
    monkeypatch.setattr(ccst, "__file__", str(fake_module_file))

    with pytest.raises(FileNotFoundError, match="Cannot locate bundled prompts"):
        ccst._discover_prompts_dir()


def test_print_migration_prompt_reminders_degrades_gracefully_when_prompts_dir_missing(
    monkeypatch, capsys
):
    """Regression test: these reminders are a nicety layered on top of an
    already-completed classification or migration, not the operation itself — a
    broken/partial install must not raise and turn a completed --write into a
    reported failure just because the bundled prompts/ directory is missing."""
    def _raise() -> Path:
        raise FileNotFoundError("Cannot locate bundled prompts/ directory.")

    monkeypatch.setattr(ccst, "_discover_prompts_dir", _raise)

    ccst._print_migration_prompt_reminders(
        Path("/some/project"), ("Update project docs", "some-prompt.md")
    )

    captured = capsys.readouterr()
    assert captured.out == ""  # nothing bogus printed to stdout (would land in --write's log)
    assert "Cannot locate bundled prompts" in captured.err


def test_print_migration_prompt_reminders_tells_user_to_run_in_project_dir(capsys):
    """Each reminder must say to run the prompt in a fresh CC session started in the project
    directory — the prompt's own Step 1 aborts if cwd isn't the project root, so a bare path
    with no such instruction invites running it inline in the current (wrong-cwd) session."""
    ccst._print_migration_prompt_reminders(
        Path("/home/chris/cc/demo"), ("Update project docs", "pdata-migration-claude-md-update.md")
    )

    captured = capsys.readouterr()
    assert "pdata-migration-claude-md-update.md" in captured.out
    assert "new Claude Code session" in captured.out
    assert "/home/chris/cc/demo" in captured.out


def test_discovery_succeeds_against_a_simulated_installed_package_layout(monkeypatch, tmp_path):
    """The three tests above ('finds the real bundled ...') only ever run against this repo's
    editable install, which points straight at src/cc_session_tools/ - `uv run python -c
    "import cc_session_tools; print(cc_session_tools.__file__)"` confirms this. That never
    exercises the layout a real `pip`/`uv tool install` produces (a flat
    <site-packages>/cc_session_tools/, no src/ prefix). Build that layout explicitly and confirm
    all three discovery functions still succeed - a regression here (e.g. a filesystem walk-up
    or a hardcoded "src" segment reintroduced) could pass the editable-install tests above while
    breaking a real installed package."""
    package_root = tmp_path / "site-packages" / "cc_session_tools"
    (package_root / "cli").mkdir(parents=True)
    (package_root / "cli" / "ccst.py").touch()
    (package_root / "skills" / "delete-sessions").mkdir(parents=True)
    (package_root / "skills" / "delete-sessions" / "SKILL.md").touch()
    (package_root / "config").mkdir()
    (package_root / "config" / "hooks-bundle.json").touch()
    (package_root / "prompts").mkdir()
    (package_root / "prompts" / "pdata-migration-claude-md-update.md").touch()

    monkeypatch.setattr(ccst, "__file__", str(package_root / "cli" / "ccst.py"))

    assert ccst._discover_source_dir() == package_root / "skills"
    assert ccst._discover_bundle() == package_root / "config" / "hooks-bundle.json"
    assert ccst._discover_prompts_dir() == package_root / "prompts"


def test_bundled_prompts_are_no_longer_placeholders():
    prompts_dir = ccst._discover_prompts_dir()
    for filename in ("pdata-migration-claude-md-update.md", "pdata-migration-skills-update.md"):
        text = (prompts_dir / filename).read_text()
        assert "PLACEHOLDER" not in text
