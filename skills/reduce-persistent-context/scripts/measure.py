"""Enumerate persistent context contributors and count their tokens."""
from dataclasses import dataclass
from pathlib import Path
import re

from tokens import token_count

# Skill descriptions are deliberately NOT a captured section: they are measured
# per-skill from disk (measure_skill_descriptions) for granularity, and those
# rows already sum to the rendered skill-descriptions block. Capturing them too
# would double-count the total.
REQUIRED_SECTIONS = [
    "DEFERRED_TOOL_NAMES", "SESSIONSTART_HOOKS", "MCP_INSTRUCTIONS",
]
SECTION_TO_CATEGORY = {
    "DEFERRED_TOOL_NAMES": "deferred_tools",
    "SESSIONSTART_HOOKS": "hooks",
    "MCP_INSTRUCTIONS": "mcp_instructions",
}
# Irreducible scaffolding the model cannot see to measure exactly: the core
# Claude Code system prompt (~2k) PLUS the built-in NON-deferred tool schemas
# that load in full every session (Workflow, Agent, Bash, AskUserQuestion,
# Read, Edit, Write, Skill, ToolSearch, ScheduleWakeup, SendUserFile). Workflow
# and Agent alone are several thousand tokens; the set totals roughly 6-7k. So
# the baseline is ~9k, not the bare-system-prompt 2.7k assumed initially.
# APPROXIMATE and config-dependent (it shifts with which built-in tools the
# harness loads). Flagged "(estimated)" in all output.
#
# Harness-only residual = system prompt + built-in non-deferred tool schemas
# (Workflow/Agent/Bash/...) + session-start injections (e.g. the superpowers
# using-superpowers body). Cannot be measured directly from inside a session.
#
# Calibrated 2026-06-20 via CALIBRATION.md from the oneshot "find-baseline"
# session: a cold first turn ("hi", cache_read=0) reported F = input 25,481 +
# cache_creation 34,624 = 60,105 Anthropic tokens for the FULL persistent
# context. A full-capture analyzer run in that session measured M = 22,197
# tiktoken of attributable contributors, so harness = F - M = 37,908.
#
# APPROXIMATE: F is Anthropic tokens while M (and the rest of the report) is
# tiktoken, so this residual absorbs the unit gap as well as the true harness
# (system prompt + built-in tool schemas + session-start injections). Re-derive
# with CALIBRATION.md if the harness changes.
HARNESS_BASELINE_TOKENS = 37908


@dataclass(frozen=True)
class Contributor:
    name: str
    category: str
    tokens: int
    detail: str = ""


def _measure_claude_md_at(root_dir: Path, name: str, category: str) -> list[Contributor]:
    recs: list[Contributor] = []
    root = root_dir / "CLAUDE.md"
    if root.exists():
        text = root.read_text()
        recs.append(Contributor(name, category, token_count(text)))
        for imp in re.findall(r"^@(\S+)", text, re.MULTILINE):
            p = (root_dir / imp).resolve()
            if p.exists():
                recs.append(Contributor(f"{imp} ({category})", category,
                                        token_count(p.read_text())))
    return recs


def measure_claude_md(claude_home: Path) -> list[Contributor]:
    """The global CLAUDE.md at the root of ~/.claude, plus its @imports."""
    return _measure_claude_md_at(claude_home, "CLAUDE.md (global)", "claude_md_global")


def measure_project_claude_md(project_root: Path) -> list[Contributor]:
    """The project-level CLAUDE.md in the audited session's cwd, plus its @imports.

    Distinct name/category from measure_claude_md() so a report covering both
    the global and a project CLAUDE.md never collapses them into one
    ambiguous "CLAUDE.md" row.
    """
    return _measure_claude_md_at(project_root, "CLAUDE.md (project)", "claude_md_project")


def _name_and_description(skill_md: Path) -> str:
    """Only `name` + `description` load at startup, not the whole frontmatter."""
    text = skill_md.read_text()
    m = re.search(r"^---\n(.*?)\n---", text, re.DOTALL)
    block = m.group(1) if m else ""
    name = re.search(r"^name:\s*(.+)$", block, re.MULTILINE)
    # description may be a `|` block scalar spanning multiple indented lines.
    desc = re.search(r"^description:\s*\|?\s*\n?((?:.*(?:\n|$))*?)(?=^\w+:|\Z)",
                     block, re.MULTILINE)
    parts = []
    if name:
        parts.append(name.group(1).strip())
    if desc:
        parts.append(desc.group(1).strip())
    return "\n".join(parts)


def measure_skill_descriptions(skills_dir: Path) -> list[Contributor]:
    recs: list[Contributor] = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name = skill_md.parent.name
        recs.append(Contributor(name, "skill_desc",
                                token_count(_name_and_description(skill_md))))
    return recs


def _split_sections(text: str) -> dict[str, str]:
    parts = re.split(r"^### (\w+)\s*$", text, flags=re.MULTILINE)
    out: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        out[parts[i]] = parts[i + 1].strip()
    return out


def parse_deferred_tools(section: str) -> list[Contributor]:
    """Split the deferred-tool names by `mcp__<server>__` prefix.

    Per-server attribution is the point: an unused MCP server's name block is a
    prime removal candidate, and the server name joins to usage data. Built-in
    deferred tools (WebFetch, Cron*, Task*, ...) collapse into one row.
    """
    server_lines: dict[str, list[str]] = {}
    builtin: list[str] = []
    for line in section.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"mcp__(.+?)__", line)
        if m:
            server_lines.setdefault(m.group(1), []).append(line)
        else:
            builtin.append(line)
    recs = [
        Contributor(server, "mcp_names", token_count("\n".join(lines)))
        for server, lines in sorted(server_lines.items())
    ]
    if builtin:
        recs.append(Contributor("builtin-deferred", "deferred_tools",
                                token_count("\n".join(builtin))))
    return recs


def measure_captured(captured_path: Path) -> list[Contributor]:
    if not captured_path.exists():
        raise ValueError(f"captured-context.txt missing at {captured_path}")
    sections = _split_sections(captured_path.read_text())
    for required in REQUIRED_SECTIONS:
        if required not in sections:
            raise ValueError(f"captured-context.txt missing section: {required}")
    recs = parse_deferred_tools(sections["DEFERRED_TOOL_NAMES"])
    recs.append(Contributor("SESSIONSTART_HOOKS", "hooks",
                            token_count(sections["SESSIONSTART_HOOKS"])))
    recs.append(Contributor("MCP_INSTRUCTIONS", "mcp_instructions",
                            token_count(sections["MCP_INSTRUCTIONS"])))
    return recs


def measure_harness_baseline() -> Contributor:
    return Contributor("system-prompt/harness", "harness",
                       HARNESS_BASELINE_TOKENS, "(estimated)")
