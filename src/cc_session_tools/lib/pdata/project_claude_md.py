"""The starting CLAUDE.md `ccst pdata init` writes into a genuinely new project."""
from __future__ import annotations

_SECTIONS = """\
## Date format

- Filenames and folder names: `yyyy.MM.dd` (with time `yyyy.MM.dd-HHmm`, partial `yyyy.MM`).
- Formal external letters: long form (for example "5 October 2026") in the header only.
- Everything else: `yyyy-MM-dd` - prose, WORKLOG, CSV/TSV and pdata date fields, frontmatter, \
JSON/YAML, commit text. Time of day is `yyyy-MM-dd HH:mm`; machine timestamps stay ISO 8601 \
(`2026-10-05T14:30:00Z`).
- Never mix formats inside one column or field.
- Exempt: identifiers containing a date (session tags, branch names, backup stamps), \
tool-generated names, existing filenames, and evidence or any document the project did not write.

## Line endings

- Standard is LF, not CRLF, for all text files (CSV, Markdown, scripts, JSON).
- Python's `csv.writer` emits CRLF by default, even with `open(..., newline='')`. Always pass \
`lineterminator='\\n'` to `csv.writer` / `csv.DictWriter`.
- Other Python defaults can also write the wrong ending: open text files with `newline='\\n'` \
when writing, and check any tool that rewrites a file.
- When adding or rewriting a text file, check it has no `\\r` (`grep -c $'\\r' <file>` should \
print 0).
"""


def seed_text(project: str) -> str:
    return f"# {project}\n\n{_SECTIONS}"
