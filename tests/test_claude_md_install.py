# tests/test_claude_md_install.py
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cc_session_tools.lib.claude_md_install import (
    MalformedBlockError,
    MarkdownAction,
    SECTION_IDS,
    install_claude_md,
    uninstall_claude_md,
    # White-box: tests assert on the exact sentinel markers the module manages.
    _SENTINEL_START,
    _SENTINEL_END,
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cc_session_tools.cli.ccst", *args],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )


def test_install_adds_block(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# My instructions\n")
    results = install_claude_md(md, apply=True)
    assert all(r.action is MarkdownAction.ADDED for r in results)
    text = md.read_text()
    assert _SENTINEL_START in text and _SENTINEL_END in text
    assert text.startswith("# My instructions")


def test_install_installs_every_registered_section_by_default(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    results = install_claude_md(md, apply=True)
    assert [r.section for r in results] == list(SECTION_IDS)
    text = md.read_text()
    for section_id in SECTION_IDS:
        assert f"<!-- CCST:{section_id} START -->" in text
        assert f"<!-- CCST:{section_id} END -->" in text


def test_install_with_sections_subset_installs_only_those(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    results = install_claude_md(md, apply=True, sections=["workflow"])
    assert [r.section for r in results] == ["workflow"]
    text = md.read_text()
    assert "<!-- CCST:workflow START -->" in text
    assert "<!-- CCST:messaging START -->" not in text
    assert "<!-- CCST:agents START -->" not in text
    assert "<!-- CCST:confirm-gate START -->" not in text


def test_install_unknown_section_raises_before_touching_file(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    with pytest.raises(ValueError):
        install_claude_md(md, apply=True, sections=["not-a-real-section"])
    assert md.read_text() == "# x\n"


def test_reinstall_is_idempotent(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=True)
    before = md.read_text()
    results = install_claude_md(md, apply=True)
    assert all(r.action is MarkdownAction.ALREADY_PRESENT for r in results)
    assert md.read_text() == before


def test_reinstall_of_one_section_is_idempotent_others_unaffected(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=True, sections=["messaging", "workflow"])
    before = md.read_text()
    results = install_claude_md(md, apply=True, sections=["workflow"])
    assert results[0].action is MarkdownAction.ALREADY_PRESENT
    assert md.read_text() == before


def test_uninstall_removes_block(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=True)
    results = uninstall_claude_md(md, apply=True)
    assert all(r.action is MarkdownAction.REMOVED for r in results)
    assert _SENTINEL_START not in md.read_text()


def test_uninstall_one_section_leaves_others_installed(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=True)
    results = uninstall_claude_md(md, apply=True, sections=["agents"])
    assert [r.section for r in results] == ["agents"]
    assert results[0].action is MarkdownAction.REMOVED
    text = md.read_text()
    assert "<!-- CCST:agents START -->" not in text
    for section_id in SECTION_IDS:
        if section_id != "agents":
            assert f"<!-- CCST:{section_id} START -->" in text


def test_install_dry_run_does_not_write(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=False)
    assert _SENTINEL_START not in md.read_text()


def test_install_creates_missing_file(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    results = install_claude_md(md, apply=True)
    assert all(r.action is MarkdownAction.ADDED for r in results)
    assert md.is_file()


def test_uninstall_dry_run_does_not_write(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    install_claude_md(md, apply=True)
    before = md.read_text()
    results = uninstall_claude_md(md, apply=False)
    assert all(r.action is MarkdownAction.REMOVED for r in results)  # would-remove (dry run)
    assert md.read_text() == before  # nothing written


def test_orphaned_start_marker_is_rejected(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    # Only a START marker, with user prose after it: editing must not swallow it.
    md.write_text(f"# x\n{_SENTINEL_START}\nimportant user prose\n")
    with pytest.raises(MalformedBlockError):
        install_claude_md(md, apply=True)
    with pytest.raises(MalformedBlockError):
        uninstall_claude_md(md, apply=True)
    # The file is untouched.
    assert "important user prose" in md.read_text()


def test_malformed_section_does_not_block_installing_an_unrelated_section(tmp_path: Path) -> None:
    """A malformed 'agents' block must not prevent an explicit, unrelated
    --section workflow install from succeeding."""
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n<!-- CCST:agents START -->\norphaned\n")
    results = install_claude_md(md, apply=True, sections=["workflow"])
    assert results[0].action is MarkdownAction.ADDED
    assert "<!-- CCST:workflow START -->" in md.read_text()
    # The malformed, untouched region is still there since it wasn't requested.
    assert "orphaned" in md.read_text()
    # But a call that does touch the malformed section still raises.
    with pytest.raises(MalformedBlockError):
        install_claude_md(md, apply=True, sections=["agents"])


# ---------- messaging section byte-for-byte pin ----------
# This must never drift silently: anyone with the pre-existing block installed
# would otherwise see a spurious REPLACED on their next install.

_MESSAGING_BLOCK = """\
<!-- CCST:messaging START -->
## Inter-session messaging

You can leave a durable message for another Claude Code session (a specific
session, a whole project, or "whoever is working on X"). Use this proactively
when you discover something relevant to another project, hand off a sub-task, or
need to coordinate with another session - do not wait to be asked.

- When a cross-session message is warranted, use the `send-session-message` skill.
  It helps you choose the recipient (session / project / description), confirm an
  ambiguous recipient with the user, and call `ccmsg send`.
- Delivered messages arrive automatically as injected context. Read a body with
  `ccmsg read <id>`. For a description-addressed proposal, confirm with the user,
  then `ccmsg claim <id>` (first claim wins).

## Cross-session task tracking

Claude Code's built-in `TaskCreate`/`TaskList`/`TaskGet`/`TaskUpdate` tools persist a task
list beyond the current conversation - do not tell the user you can't track tasks across
sessions before checking for these.

- They are usually deferred tools: if they aren't already available, use `ToolSearch` with
  `select:TaskCreate,TaskList,TaskGet,TaskUpdate` to load them before assuming they're absent.
- Persistence is scoped by `CLAUDE_CODE_TASK_LIST_ID`, which `ccd`/`ccr` set from the current
  project directory's name. Sessions launched in the same project (via `ccd`/`ccr`) share one
  task list; sessions in different projects do not.
- When the user wants a task, todo, or reminder tracked beyond this single conversation, use
  `TaskCreate` (and `TaskList`/`TaskGet`/`TaskUpdate` to check or update it later) instead of
  the ephemeral, session-only `TodoWrite` tool, and instead of saying this isn't possible.
<!-- CCST:messaging END -->
"""


def test_messaging_block_is_byte_for_byte_pinned(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# My instructions\n")
    install_claude_md(md, apply=True, sections=["messaging"])
    text = md.read_text()
    assert text == "# My instructions\n" + _MESSAGING_BLOCK


def _date_format_body(tmp_path: Path) -> str:
    md = tmp_path / "CLAUDE.md"
    install_claude_md(md, apply=True, sections=["date-format"])
    text = md.read_text()
    start = text.index("<!-- CCST:date-format START -->")
    end = text.index("<!-- CCST:date-format END -->")
    return text[start:end]


def test_date_format_section_states_the_convention(tmp_path: Path) -> None:
    body = _date_format_body(tmp_path)
    for required in (
        "yyyy.MM.dd", "yyyy.MM.dd-HHmm", "yyyy.MM", "yyyy-MM-dd", "yyyy-MM-dd HH:mm",
        "ISO 8601", "long form", "Never mix formats inside one column or one field",
        "session tags, branch names, backup", "names generated by tools you do not control",
        "existing filenames", "evidence", "conversion prompt",
    ):
        assert required in body, required


def test_date_format_section_has_no_personal_identifiers(tmp_path: Path) -> None:
    body = _date_format_body(tmp_path)
    for pattern in ("/Users/", "/home/", "@", "C:\\"):
        assert pattern not in body, pattern


def test_date_format_section_installs_alone_and_leaves_others_untouched(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    install_claude_md(md, apply=True, sections=["messaging"])
    before = md.read_text()
    install_claude_md(md, apply=True, sections=["date-format"])
    after = md.read_text()
    assert after.startswith(before)
    assert "<!-- CCST:date-format START -->" in after


def test_date_format_section_reinstall_is_idempotent_and_uninstalls_alone(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    install_claude_md(md, apply=True)
    first = md.read_text()
    install_claude_md(md, apply=True, sections=["date-format"])
    assert md.read_text() == first
    uninstall_claude_md(md, apply=True, sections=["date-format"])
    text = md.read_text()
    assert "CCST:date-format" not in text
    assert "<!-- CCST:confirm-gate START -->" in text


# ---------- CLI integration ----------

def test_cli_install_dry_run_reports_all_sections(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    result = _run("claude-md", "install", "--target", str(md))
    assert result.returncode == 0
    assert "Dry run" in result.stdout
    for section_id in SECTION_IDS:
        assert f"[{section_id}]" in result.stdout
    assert _SENTINEL_START not in md.read_text()


def test_cli_install_apply_writes_all_sections(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    result = _run("claude-md", "install", "--target", str(md), "--apply")
    assert result.returncode == 0
    text = md.read_text()
    for section_id in SECTION_IDS:
        assert f"<!-- CCST:{section_id} START -->" in text


def test_cli_install_with_section_flag_installs_only_that_section(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    result = _run("claude-md", "install", "--target", str(md), "--section", "agents", "--apply")
    assert result.returncode == 0
    text = md.read_text()
    assert "<!-- CCST:agents START -->" in text
    assert "<!-- CCST:messaging START -->" not in text


def test_cli_install_with_repeated_section_flag_installs_each(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    result = _run(
        "claude-md", "install", "--target", str(md),
        "--section", "agents", "--section", "confirm-gate", "--apply",
    )
    assert result.returncode == 0
    text = md.read_text()
    assert "<!-- CCST:agents START -->" in text
    assert "<!-- CCST:confirm-gate START -->" in text
    assert "<!-- CCST:messaging START -->" not in text
    assert "<!-- CCST:workflow START -->" not in text


def test_cli_install_rejects_unknown_section() -> None:
    result = _run("claude-md", "install", "--section", "not-a-real-section")
    assert result.returncode != 0
    assert "not-a-real-section" in result.stderr.lower() or "invalid choice" in result.stderr.lower()


def test_cli_uninstall_with_section_flag_removes_only_that_section(tmp_path: Path) -> None:
    md = tmp_path / "CLAUDE.md"
    md.write_text("# x\n")
    _run("claude-md", "install", "--target", str(md), "--apply")
    result = _run("claude-md", "uninstall", "--target", str(md), "--section", "agents", "--apply")
    assert result.returncode == 0
    text = md.read_text()
    assert "<!-- CCST:agents START -->" not in text
    assert "<!-- CCST:workflow START -->" in text
