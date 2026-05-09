from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import rules


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    monkeypatch.setenv("HOME", str(home))
    # rules captures DEFAULT_ROOTS_FILE at import time, so we have to patch
    # ROOTS_FILE for tests that exercise check_session_init.
    monkeypatch.setattr(rules, "ROOTS_FILE", home / ".claude" / "cc-session-roots.txt")
    return home


@pytest.fixture
def roots_file(tmp_home: Path, tmp_path: Path) -> Path:
    root = tmp_path / "projects-root"
    root.mkdir()
    rf = tmp_home / ".claude" / "cc-session-roots.txt"
    rf.write_text(f"{root}\n")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(rules, "load_session_roots",
                   lambda roots_file=rf: [root.resolve()])
    yield rf
    monkey.undo()


@pytest.fixture
def projects_root(tmp_path: Path) -> Path:
    return tmp_path / "projects-root"


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
    def test_passes_when_cwd_under_root_and_tag_clean(self, tmp_home, roots_file, projects_root, monkeypatch):
        monkeypatch.setattr(rules, "is_strict_root", lambda r: False)
        cwd = projects_root / "myproj"
        cwd.mkdir()
        ok, errors = rules.check_session_init(cwd.resolve(), "any-tag")
        assert ok, errors

    def test_fails_when_tag_has_spaces(self, tmp_home, roots_file, projects_root, monkeypatch):
        monkeypatch.setattr(rules, "is_strict_root", lambda r: False)
        cwd = projects_root / "myproj"
        cwd.mkdir()
        ok, errors = rules.check_session_init(cwd.resolve(), "tag with space")
        assert not ok
        assert any("must not contain spaces" in e for e in errors)

    def test_force_skips_root_check(self, tmp_home, roots_file, monkeypatch):
        monkeypatch.setattr(rules, "is_strict_root", lambda r: False)
        outside = Path("/tmp/no-such-root/proj").resolve() if Path("/tmp").exists() else Path.cwd()
        ok, _ = rules.check_session_init(outside, "any-tag", force=True)
        assert ok

    def test_strict_root_requires_project_prefix(self, tmp_home, roots_file, projects_root, monkeypatch):
        monkeypatch.setattr(rules, "is_strict_root", lambda r: True)
        cwd = projects_root / "oneshot"
        cwd.mkdir()
        ok, errors = rules.check_session_init(cwd.resolve(), "wrong-prefix")
        assert not ok
        assert any("must start with 'oneshot-'" in e for e in errors)
