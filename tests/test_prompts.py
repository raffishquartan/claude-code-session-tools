from __future__ import annotations

import io
from pathlib import Path

import pytest

from cc_session_tools.lib import prompts


@pytest.fixture
def fake_strict_root(tmp_path, monkeypatch):
    sr = tmp_path / "strict"
    sr.mkdir()
    monkeypatch.setattr(prompts, "strict_root_path", lambda: sr)
    return sr


def test_returns_tag_unchanged_when_not_under_strict_root(tmp_path, monkeypatch):
    monkeypatch.setattr(prompts, "strict_root_path", lambda: tmp_path / "elsewhere")
    cwd = tmp_path / "myproj"
    cwd.mkdir()
    assert prompts.maybe_correct_tag(cwd, "tag-abc") == "tag-abc"


def test_returns_tag_unchanged_when_first_term_matches_project(fake_strict_root):
    cwd = fake_strict_root / "oneshot"
    cwd.mkdir()
    assert prompts.maybe_correct_tag(cwd, "oneshot-fix-bashrc") == "oneshot-fix-bashrc"


def test_typo_prompt_when_first_term_within_distance_2_and_user_accepts(fake_strict_root, capsys):
    cwd = fake_strict_root / "oneshot"
    cwd.mkdir()
    result = prompts.maybe_correct_tag(
        cwd, "oneshet-fix-bashrc", input_fn=lambda: "y"
    )
    assert result == "oneshot-fix-bashrc"
    err = capsys.readouterr().err
    assert "looks like a typo" in err
    assert "oneshot" in err


def test_typo_prompt_decline_exits(fake_strict_root):
    cwd = fake_strict_root / "oneshot"
    cwd.mkdir()
    with pytest.raises(SystemExit):
        prompts.maybe_correct_tag(cwd, "oneshet-foo", input_fn=lambda: "")


def test_typo_prompt_decline_with_n_exits(fake_strict_root):
    cwd = fake_strict_root / "oneshot"
    cwd.mkdir()
    with pytest.raises(SystemExit):
        prompts.maybe_correct_tag(cwd, "oneshet-foo", input_fn=lambda: "n")


def test_missing_prefix_prompt_when_first_term_far_from_all_projects_and_user_accepts(
    fake_strict_root, capsys
):
    (fake_strict_root / "oneshot").mkdir()
    (fake_strict_root / "coparenting").mkdir()
    (fake_strict_root / "pbt").mkdir()
    cwd = fake_strict_root / "oneshot"
    result = prompts.maybe_correct_tag(
        cwd, "fix-bashrc", input_fn=lambda: "y"
    )
    assert result == "oneshot-fix-bashrc"
    err = capsys.readouterr().err
    assert "not a recognised project under the strict" in err


def test_missing_prefix_prompt_decline_exits(fake_strict_root):
    (fake_strict_root / "oneshot").mkdir()
    (fake_strict_root / "coparenting").mkdir()
    cwd = fake_strict_root / "oneshot"
    with pytest.raises(SystemExit):
        prompts.maybe_correct_tag(cwd, "fix-bashrc", input_fn=lambda: "")


def test_no_prompt_when_first_term_close_to_another_project(fake_strict_root):
    # "coparentig" is distance 1 from "coparenting" (sibling project), but
    # we're in oneshot/. We don't auto-correct cross-project; just leave the
    # tag alone (the validator may still complain, but no prompt fires).
    (fake_strict_root / "oneshot").mkdir()
    (fake_strict_root / "coparenting").mkdir()
    cwd = fake_strict_root / "oneshot"
    assert prompts.maybe_correct_tag(cwd, "coparentig-foo") == "coparentig-foo"


def test_no_prompt_when_no_other_projects_and_first_term_far(fake_strict_root, capsys):
    # First term is far from current project (the only project), so the
    # missing-prefix prompt fires (vacuously true that all 0 others are far).
    cwd = fake_strict_root / "oneshot"
    cwd.mkdir()
    result = prompts.maybe_correct_tag(cwd, "wildlydifferent", input_fn=lambda: "y")
    assert result == "oneshot-wildlydifferent"
