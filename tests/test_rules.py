from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import rules


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A configured loose root with the env var set to point at it."""
    root = tmp_path / "projects-root"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(root))
    return root


class TestEncodeCwd:
    def test_encodes_absolute_path(self):
        assert rules.encode_cwd("/mnt/c/Users/foo") == "-mnt-c-Users-foo"

    def test_encodes_root_only(self):
        assert rules.encode_cwd("/") == "-"

    def test_rejects_relative(self):
        with pytest.raises(ValueError, match="Expected absolute path"):
            rules.encode_cwd("relative/path")


class TestValidateNewTag:
    def test_accepts_well_formed(self):
        assert rules.validate_new_tag("20260503-good-tag", "20260503-old") == ""

    def test_rejects_spaces(self):
        assert "must not contain spaces" in rules.validate_new_tag("20260503-bad tag", "20260503-old")

    def test_rejects_underscores(self):
        assert "underscores" in rules.validate_new_tag("20260503-bad_tag", "20260503-old")

    def test_rejects_double_dashes(self):
        assert "double-dashes" in rules.validate_new_tag("20260503-bad--tag", "20260503-old")

    def test_rejects_trailing_dash(self):
        err = rules.validate_new_tag("20260503-bad-", "20260503-old")
        assert "end with a dash" in err

    def test_rejects_no_date_prefix(self):
        assert "YYYYMMDD" in rules.validate_new_tag("just-a-name", "20260503-old")

    def test_rejects_changed_date_prefix(self):
        assert "date prefix is immutable" in rules.validate_new_tag("20260504-old-tag", "20260503-old-tag")


class TestValidateStrictProjectName:
    def test_accepts_lowercase_alphanumeric(self):
        assert rules.validate_strict_project_name("oneshot") == ""
        assert rules.validate_strict_project_name("a1") == ""

    def test_rejects_uppercase(self):
        assert "must match" in rules.validate_strict_project_name("OneShot")

    def test_rejects_dashes(self):
        assert "must match" in rules.validate_strict_project_name("one-shot")


class TestValidateStrictTagSuffix:
    def test_accepts_project_prefix_with_label(self):
        assert rules.validate_strict_tag_suffix("oneshot-config-cleanup", "oneshot") == ""

    def test_rejects_missing_prefix(self):
        err = rules.validate_strict_tag_suffix("config-cleanup", "oneshot")
        assert "must start with" in err

    def test_rejects_bare_project_name(self):
        err = rules.validate_strict_tag_suffix("oneshot-", "oneshot")
        assert "descriptive label" in err

    def test_rejects_dashes_only_after_prefix(self):
        err = rules.validate_strict_tag_suffix("oneshot---", "oneshot")
        assert "alphanumeric character" in err


class TestValidateTagSuffixNoSpaces:
    def test_accepts_no_spaces(self):
        assert rules.validate_tag_suffix_no_spaces("foo-bar") == ""

    def test_rejects_with_spaces(self):
        assert "must not contain spaces" in rules.validate_tag_suffix_no_spaces("foo bar")


class TestMatchedSessionRoot:
    def test_returns_root_for_direct_subdir(self, tmp_path):
        root = tmp_path / "r"
        root.mkdir()
        cwd = root / "p"
        cwd.mkdir()
        assert rules.matched_session_root(cwd, [root]) == root

    def test_returns_none_when_grandchild(self, tmp_path):
        root = tmp_path / "r"
        nested = root / "child" / "grand"
        nested.mkdir(parents=True)
        assert rules.matched_session_root(nested, [root]) is None


class TestCheckSessionInit:
    def test_passes_when_cwd_under_repo_root_and_tag_clean(self, projects_root):
        cwd = projects_root / "myproj"
        cwd.mkdir()
        ok, errors = rules.check_session_init(cwd.resolve(), "any-tag")
        assert ok, errors

    def test_fails_when_tag_has_spaces(self, projects_root):
        cwd = projects_root / "myproj"
        cwd.mkdir()
        ok, errors = rules.check_session_init(cwd.resolve(), "tag with space")
        assert not ok
        assert any("must not contain spaces" in e for e in errors)

    def test_force_skips_root_check(self):
        outside = Path("/tmp/no-such-root/proj").resolve() if Path("/tmp").exists() else Path.cwd()
        ok, _ = rules.check_session_init(outside, "any-tag", force=True)
        assert ok

    def test_fails_with_helpful_error_when_no_roots_configured(self, tmp_path):
        # Both env vars unset (autouse fixture clears them).
        ok, errors = rules.check_session_init(tmp_path, "any-tag")
        assert not ok
        joined = "\n".join(errors)
        assert "CLAUDE_SESSION_TOOLS_REPO_ROOT" in joined
        assert "CLAUDE_SESSION_TOOLS_PROJ_ROOT" in joined

    def test_strict_root_requires_project_prefix(self, tmp_path, monkeypatch):
        proj_root = tmp_path / "proj-root"
        proj_root.mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(proj_root))
        cwd = proj_root / "oneshot"
        cwd.mkdir()
        ok, errors = rules.check_session_init(cwd.resolve(), "wrong-prefix")
        assert not ok
        assert any("must start with 'oneshot-'" in e for e in errors)

    def test_strict_root_accepts_project_prefixed_tag(self, tmp_path, monkeypatch):
        proj_root = tmp_path / "proj-root"
        proj_root.mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(proj_root))
        cwd = proj_root / "oneshot"
        cwd.mkdir()
        ok, errors = rules.check_session_init(cwd.resolve(), "oneshot-do-the-thing")
        assert ok, errors

    def test_loose_root_does_not_apply_strict_naming(self, projects_root):
        # projects_root is configured as REPO_ROOT (loose), so any tag is fine.
        cwd = projects_root / "MyProject-with-dashes"
        cwd.mkdir()
        ok, errors = rules.check_session_init(cwd.resolve(), "anything-goes")
        assert ok, errors
