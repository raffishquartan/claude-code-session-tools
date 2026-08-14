"""Tests for ccst.py's `_discover_source_dir`/`_discover_bundle` — must resolve the
bundled skills/ and config/ correctly whether cc_session_tools is running from an
editable source checkout or an installed wheel, since both layouts place
cc_session_tools/skills/ and cc_session_tools/config/ at the same relative depth from
this module (src/cc_session_tools/cli/ccst.py -> ../skills, ../config)."""
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
