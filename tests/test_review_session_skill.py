"""The bundled review-session-and-improve-claude-setup skill: packaged like every other bundled
skill, checked by doctor, and wired only to `ccst` commands that exist."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import cc_session_tools
from cc_session_tools.lib import doctor
from cc_session_tools.lib.doctor import Status
from cc_session_tools.lib.self_improvement import signals

NAME = "review-session-and-improve-claude-setup"
SKILLS = Path(cc_session_tools.__file__).parent / "skills"
SKILL = SKILLS / NAME / "SKILL.md"


def _ccst(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True, text=True, check=False,
    )


def test_skill_is_bundled_with_matching_frontmatter_name() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert f"\nname: {NAME}\n" in text.split("\n---\n", 1)[0] + "\n"
    assert signals.PERIODIC_REVIEW_COMMAND == f"/{NAME} periodic"


def test_skills_install_links_it_into_an_empty_target(tmp_path: Path) -> None:
    target = tmp_path / "skills"
    proc = _ccst("skills", "install", "--target", str(target), "--apply")
    assert proc.returncode == 0, proc.stderr
    link = target / NAME
    assert link.is_symlink() and link.resolve() == SKILL.parent.resolve()
    assert doctor.check_skill_symlink(NAME, SKILL.parent, target).status is Status.OK


def test_a_copied_directory_is_left_untouched_and_reported(tmp_path: Path) -> None:
    target = tmp_path / "skills"
    copied = target / NAME
    copied.mkdir(parents=True)
    (copied / "SKILL.md").write_text("an older copy\n")
    proc = _ccst("skills", "install", "--target", str(target), "--apply")
    assert proc.returncode == 1
    assert f"{copied} exists and is not a symlink; use --force to move it aside" in proc.stderr
    assert not copied.is_symlink() and (copied / "SKILL.md").read_text() == "an older copy\n"
    result = doctor.check_skill_symlink(NAME, SKILL.parent, target)
    assert result.status is Status.FAIL and "is not a symlink" in result.reason


def test_skill_names_only_existing_self_improvement_verbs_and_no_scripts() -> None:
    text = SKILL.read_text(encoding="utf-8")
    verbs = set(re.findall(r"ccst self-improvement ([a-z-]+)", text))
    assert {"status", "init", "record-review"} <= verbs
    help_text = _ccst("self-improvement", "--help").stdout
    for verb in verbs:
        assert verb in help_text, verb
    assert ".sh" not in text
    assert "printf" not in text and "awk" not in text


def test_record_review_example_in_the_skill_is_accepted(tmp_path: Path) -> None:
    text = SKILL.read_text(encoding="utf-8")
    examples = re.findall(r"ccst self-improvement record-review [^\n`]+", text)
    assert examples
    env = {**os.environ, "CCST_DATA_HOME": str(tmp_path)}
    for example in examples:
        args = example.split()[1:]
        args = [a if not a.startswith("<") else "0" for a in args]
        proc = subprocess.run(
            [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
            capture_output=True, text=True, check=False, env=env,
        )
        assert proc.returncode == 0, (example, proc.stderr)
