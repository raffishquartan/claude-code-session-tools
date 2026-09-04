## Purpose

Defines the content contract of the sentinel-delimited CCST block that `ccst install-everything
--apply` (via `claude_md_install.py`) merges into a user's `~/.claude/CLAUDE.md`, so installed
sessions are told about CCST-provided capabilities rather than defaulting to ephemeral-only
behavior.

## ADDED Requirements

### Requirement: The fragment documents cross-session task tracking
The CCST-managed CLAUDE.md block SHALL include a section documenting Claude Code's
`TaskCreate`/`TaskList`/`TaskGet`/`TaskUpdate` tools as genuine cross-session persistent task
tracking, in the same style as the existing "Inter-session messaging" section (what the tools are,
how to discover them via `ToolSearch`, and that they should be used instead of telling the user
task tracking beyond the current session isn't possible).

#### Scenario: Fragment installed via `ccst install-everything --apply`
- **WHEN** the CCST block is written or updated in `~/.claude/CLAUDE.md`
- **THEN** it contains a section on `TaskCreate`/`TaskList`/`TaskGet`/`TaskUpdate` alongside the
  existing "Inter-session messaging" section, within the same sentinel-delimited managed block

#### Scenario: Fragment states the actual persistence scope
- **WHEN** the task-tracking section describes how far a task persists
- **THEN** it states that persistence is scoped by `CLAUDE_CODE_TASK_LIST_ID` - which `ccd`/`ccr`
  set from the project directory's name (`lib/tasklist.id_for_project`) - so sessions in the same
  project share a task list and sessions in different projects do not, rather than describing
  persistence as either purely per-session or unconditionally global
