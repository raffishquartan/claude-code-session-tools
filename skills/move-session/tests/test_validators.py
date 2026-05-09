"""Pure-function tests: validators, encoders, parsers."""
from __future__ import annotations

import pytest


class TestValidateNewTag:
    def test_accepts_well_formed_tag(self, ms):
        assert ms.validate_new_tag("20260503-good-tag", "20260503-old") == ""

    def test_accepts_alphanumeric_only_suffix(self, ms):
        assert ms.validate_new_tag("20260503-tag2", "20260503-old") == ""

    def test_rejects_spaces(self, ms):
        err = ms.validate_new_tag("20260503-bad tag", "20260503-old")
        assert "must not contain spaces" in err

    def test_rejects_underscores(self, ms):
        err = ms.validate_new_tag("20260503-bad_tag", "20260503-old")
        assert "underscores" in err

    def test_rejects_double_dashes(self, ms):
        err = ms.validate_new_tag("20260503-bad--tag", "20260503-old")
        assert "double-dashes" in err

    def test_rejects_trailing_dash(self, ms):
        err = ms.validate_new_tag("20260503-bad-", "20260503-old")
        assert "trailing dash" in err.lower() or "end with a dash" in err

    def test_rejects_no_date_prefix(self, ms):
        err = ms.validate_new_tag("just-a-name", "20260503-old")
        assert "YYYYMMDD" in err

    def test_rejects_changed_date_prefix(self, ms):
        err = ms.validate_new_tag("20260504-old-tag", "20260503-old-tag")
        assert "date prefix is immutable" in err

    def test_rejects_starting_dash(self, ms):
        err = ms.validate_new_tag("20260503--leading", "20260503-old")
        # could be caught by either the double-dash check or the regex
        assert err != ""


class TestEncodeCwd:
    def test_encodes_absolute_path(self, ms):
        assert ms.encode_cwd("/mnt/c/Users/foo") == "-mnt-c-Users-foo"

    def test_encodes_root_only(self, ms):
        assert ms.encode_cwd("/") == "-"

    def test_rejects_relative(self, ms):
        with pytest.raises(ValueError, match="Expected absolute path"):
            ms.encode_cwd("relative/path")


class TestFirstUserText:
    def test_string_content(self, ms):
        rec = {"message": {"role": "user", "content": "hello"}}
        assert ms.first_user_text(rec) == "hello"

    def test_list_content_with_text_block(self, ms):
        rec = {"message": {"content": [{"type": "text", "text": "hi"}]}}
        assert ms.first_user_text(rec) == "hi"

    def test_list_content_skips_non_text(self, ms):
        rec = {"message": {"content": [
            {"type": "image", "url": "x"},
            {"type": "text", "text": "found me"},
        ]}}
        assert ms.first_user_text(rec) == "found me"

    def test_missing_message(self, ms):
        assert ms.first_user_text({}) == ""

    def test_message_not_dict(self, ms):
        assert ms.first_user_text({"message": "string"}) == ""


class TestIsHookSecurityCheck:
    def test_recognises_hook_security_prefix(self, ms):
        s = {"first_user": "Review this shell command for security risks: ls"}
        assert ms.is_hook_security_check(s) is True

    def test_normal_user_not_hook(self, ms):
        s = {"first_user": "Hello, please do X"}
        assert ms.is_hook_security_check(s) is False

    def test_empty_first_user(self, ms):
        assert ms.is_hook_security_check({"first_user": ""}) is False


class TestMatchedSessionRoot:
    def test_returns_root_when_direct_subdir(self, ms, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        cwd = root / "project"
        cwd.mkdir()
        assert ms.matched_session_root(cwd, [root]) == root

    def test_returns_none_when_not_under_any_root(self, ms, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        other = tmp_path / "other" / "project"
        other.mkdir(parents=True)
        assert ms.matched_session_root(other, [root]) is None

    def test_returns_none_for_nested_grandchild(self, ms, tmp_path):
        # cwd must be a *direct* child of a root.
        root = tmp_path / "root"
        nested = root / "child" / "grand"
        nested.mkdir(parents=True)
        assert ms.matched_session_root(nested, [root]) is None


class TestValidateStrictProjectName:
    def test_accepts_lowercase_alphanumeric(self, ms):
        assert ms.validate_strict_project_name("pbt") == ""
        assert ms.validate_strict_project_name("project99") == ""
        assert ms.validate_strict_project_name("a1") == ""

    def test_rejects_uppercase(self, ms):
        err = ms.validate_strict_project_name("Project")
        assert "[a-z0-9]+" in err

    def test_rejects_dashes(self, ms):
        err = ms.validate_strict_project_name("my-project")
        assert "[a-z0-9]+" in err

    def test_rejects_underscores(self, ms):
        err = ms.validate_strict_project_name("my_project")
        assert err != ""

    def test_rejects_empty(self, ms):
        err = ms.validate_strict_project_name("")
        assert err != ""


class TestValidateStrictTagSuffix:
    def test_rejects_exact_project_name_no_label(self, ms):
        # New rule: tag must be `<project>-<descriptor>` - bare project name
        # alone is rejected because there's no descriptor.
        err = ms.validate_strict_tag_suffix("pbt", "pbt")
        assert err != ""

    def test_accepts_project_dash_prefix(self, ms):
        assert ms.validate_strict_tag_suffix("pbt-followup", "pbt") == ""
        assert ms.validate_strict_tag_suffix("pbt-x-y-z", "pbt") == ""

    def test_rejects_wrong_prefix(self, ms):
        err = ms.validate_strict_tag_suffix("other-thing", "pbt")
        assert "must start with" in err
        assert "pbt-" in err

    def test_rejects_project_substring_at_other_position(self, ms):
        err = ms.validate_strict_tag_suffix("foo-pbt", "pbt")
        assert err != ""

    def test_rejects_project_no_separator(self, ms):
        # tag "pbtfoo" doesn't start with "pbt-"
        err = ms.validate_strict_tag_suffix("pbtfoo", "pbt")
        assert err != ""

    def test_rejects_dash_with_empty_label(self, ms):
        # "pbt-" has the prefix but no descriptive label after it.
        err = ms.validate_strict_tag_suffix("pbt-", "pbt")
        assert err != ""

    def test_rejects_dash_with_non_alnum_label(self, ms):
        # "pbt---" has the prefix but the rest contains no alphanumeric.
        err = ms.validate_strict_tag_suffix("pbt---", "pbt")
        assert err != ""


class TestValidateTagSuffixNoSpaces:
    def test_accepts_dashed(self, ms):
        assert ms.validate_tag_suffix_no_spaces("config-cleanup") == ""

    def test_rejects_spaces(self, ms):
        err = ms.validate_tag_suffix_no_spaces("bad tag")
        assert "spaces" in err


class TestIsStrictRoot:
    def test_returns_false_when_proj_root_does_not_resolve(self, ms, tmp_path, monkeypatch):
        # Point PROJ_ROOT_ENV at a non-existent path. proj_root() should
        # return None, so no root can be the strict root.
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(tmp_path / "no-such-dir"))
        assert ms.is_strict_root(tmp_path) is False

    def test_returns_false_when_proj_root_unset(self, ms, tmp_path, monkeypatch):
        # No PROJ root configured at all - is_strict_root returns False
        # for any path.
        monkeypatch.delenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", raising=False)
        assert ms.is_strict_root(tmp_path) is False

    def test_returns_true_when_root_matches_resolved_proj_root(self, ms, tmp_path, monkeypatch):
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)
        # PROJ_ROOT_ENV may point at a symlink; proj_root() resolves it.
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(link))
        assert ms.is_strict_root(target) is True
        assert ms.is_strict_root(tmp_path / "other") is False


class TestCheckSessionInit:
    def test_passes_under_strict_root_with_matching_tag(self, ms, tmp_path, monkeypatch):
        cc_root = tmp_path / "cc"
        cc_root.mkdir()
        (cc_root / "pbt").mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(cc_root))

        ok, errors = ms.check_session_init(cc_root / "pbt", "pbt-followup")
        assert ok, errors

    def test_fails_under_strict_root_with_wrong_prefix(self, ms, tmp_path, monkeypatch):
        cc_root = tmp_path / "cc"
        cc_root.mkdir()
        (cc_root / "pbt").mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(cc_root))

        ok, errors = ms.check_session_init(cc_root / "pbt", "other-thing")
        assert not ok
        assert any("must start with" in e and "pbt-" in e for e in errors)

    def test_fails_under_strict_root_with_invalid_project_name(self, ms, tmp_path, monkeypatch):
        cc_root = tmp_path / "cc"
        cc_root.mkdir()
        (cc_root / "Bad-Name").mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(cc_root))

        ok, errors = ms.check_session_init(cc_root / "Bad-Name", "Bad-Name")
        assert not ok
        assert any("[a-z0-9]+" in e for e in errors)

    def test_passes_under_non_strict_root_with_any_project_and_tag(self, ms, tmp_path, monkeypatch):
        repos_root = tmp_path / "repos"
        repos_root.mkdir()
        (repos_root / "My-Repo").mkdir()
        # REPO root is loose; PROJ root unset so My-Repo isn't strict.
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos_root))

        ok, errors = ms.check_session_init(repos_root / "My-Repo", "anything-goes")
        assert ok, errors

    def test_fails_when_cwd_not_under_any_root(self, ms, tmp_path, monkeypatch):
        configured = tmp_path / "configured-root"
        configured.mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(configured))

        random = tmp_path / "random" / "project"
        random.mkdir(parents=True)
        ok, errors = ms.check_session_init(random, "mytag")
        assert not ok
        assert any("not a direct subdirectory" in e for e in errors)

    def test_force_bypasses_root_and_strict_rules(self, ms, tmp_path, monkeypatch):
        cc_root = tmp_path / "cc"
        cc_root.mkdir()
        (cc_root / "Bad-Name").mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(cc_root))

        # Bad project name + tag wouldn't match: should pass with force.
        ok, errors = ms.check_session_init(cc_root / "Bad-Name", "anything", force=True)
        assert ok, errors

        # And from a totally non-root path:
        random = tmp_path / "random"
        random.mkdir()
        ok, errors = ms.check_session_init(random, "anything", force=True)
        assert ok, errors

    def test_force_does_not_bypass_space_check(self, ms, tmp_path, monkeypatch):
        random = tmp_path / "random"
        random.mkdir()
        # No need to set roots; force skips that branch.
        ok, errors = ms.check_session_init(random, "bad tag", force=True)
        assert not ok
        assert any("spaces" in e for e in errors)


class TestCheckSessionDestination:
    def test_passes_under_non_strict_root_with_any_tag(self, ms, tmp_path, monkeypatch):
        repos_root = tmp_path / "repos"
        repos_root.mkdir()
        (repos_root / "My-Repo").mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos_root))

        ok, errors = ms.check_session_destination(
            repos_root / "My-Repo", "20260504-anything", "20260504-source"
        )
        assert ok, errors

    def test_fails_under_strict_root_with_wrong_tag_prefix(self, ms, tmp_path, monkeypatch):
        cc_root = tmp_path / "cc"
        cc_root.mkdir()
        (cc_root / "dea").mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(cc_root))

        ok, errors = ms.check_session_destination(
            cc_root / "dea", "20260504-pbt-foo", "20260504-pbt-foo"
        )
        assert not ok
        assert any("must start with" in e and "dea-" in e for e in errors)

    def test_passes_under_strict_root_with_matching_tag(self, ms, tmp_path, monkeypatch):
        cc_root = tmp_path / "cc"
        cc_root.mkdir()
        (cc_root / "pbt").mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(cc_root))

        ok, errors = ms.check_session_destination(
            cc_root / "pbt", "20260504-pbt-followup", "20260504-pbt-foo"
        )
        assert ok, errors

    def test_immutable_date_prefix_always_enforced_even_with_force(self, ms, tmp_path):
        # No roots needed for this branch.
        ok, errors = ms.check_session_destination(
            tmp_path / "anywhere", "20260601-renamed", "20260504-original",
            force=True,
        )
        assert not ok
        assert any("date prefix is immutable" in e for e in errors)

    def test_force_bypasses_root_and_strict_rules(self, ms, tmp_path, monkeypatch):
        cc_root = tmp_path / "cc"
        cc_root.mkdir()
        (cc_root / "dea").mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_PROJ_ROOT", str(cc_root))

        # Wrong tag prefix under strict root; force makes it pass.
        ok, errors = ms.check_session_destination(
            cc_root / "dea", "20260504-pbt-foo", "20260504-pbt-foo",
            force=True,
        )
        assert ok, errors

    def test_no_rename_skips_new_tag_check(self, ms, tmp_path, monkeypatch):
        repos_root = tmp_path / "repos"
        repos_root.mkdir()
        (repos_root / "myrepo").mkdir()
        monkeypatch.setenv("CLAUDE_SESSION_TOOLS_REPO_ROOT", str(repos_root))

        # src_tag=None means caller is signalling "no rename"; even an oddly
        # formatted tag should be accepted (the format check only runs on rename).
        ok, errors = ms.check_session_destination(
            repos_root / "myrepo", "20260504-some_tag_with_underscore", src_tag=None
        )
        assert ok, errors
