"""The CLAUDE.md `ccst pdata init` seeds into a genuinely new project."""
from __future__ import annotations

import re

from cc_session_tools.lib.pdata import project_claude_md


def test_seed_starts_with_the_project_heading_then_both_sections() -> None:
    text = project_claude_md.seed_text("demo")
    assert text.startswith("# demo\n\n## Date format\n")
    assert "\n## Line endings\n" in text
    assert text.index("## Date format") < text.index("## Line endings")


def test_date_section_states_the_three_clauses_and_the_exemptions() -> None:
    text = project_claude_md.seed_text("demo")
    for fragment in (
        "`yyyy.MM.dd`", "`yyyy.MM.dd-HHmm`", "`yyyy.MM`",
        "Formal external letters", "long form",
        "`yyyy-MM-dd`", "`yyyy-MM-dd HH:mm`", "ISO 8601",
        "Never mix formats inside one column or field",
        "identifiers containing a date", "tool-generated names", "existing filenames",
        "evidence",
    ):
        assert fragment in text, fragment


def test_line_endings_section_states_the_four_rules() -> None:
    text = project_claude_md.seed_text("demo")
    for fragment in (
        "LF, not CRLF", "csv.writer", "csv.DictWriter", "lineterminator='\\n'",
        "newline='\\n'", "no `\\r`",
    ):
        assert fragment in text, fragment


def test_seed_has_lf_endings_only() -> None:
    assert "\r" not in project_claude_md.seed_text("demo")


def test_seed_names_no_personal_path_and_links_nothing_outside_the_project() -> None:
    text = project_claude_md.seed_text("demo")
    assert not re.search(r"/Users/|/home/|[A-Za-z]:\\|OneDrive|https?://", text)
