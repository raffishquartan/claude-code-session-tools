# install/claude-md-fragment Specification

## Purpose

Defines the content contract of the sentinel-delimited CCST block that `ccst install-everything
--apply` (via `claude_md_install.py`) merges into a user's `~/.claude/CLAUDE.md`, so installed
sessions are told about CCST-provided capabilities rather than defaulting to ephemeral-only
behavior.

## Requirements

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

### Requirement: The fragment is organized as independently-installable sections
The CCST-managed content in `~/.claude/CLAUDE.md` SHALL be organized as a registry of named
sections, each delimited by its own `<!-- CCST:<section-id> START -->` / `<!-- CCST:<section-id>
END -->` sentinel pair, so any subset can be installed or removed without affecting the others.
Installing or uninstalling with no section selected SHALL process every registered section.
The existing `messaging` section's sentinel text and body content SHALL be unchanged by this
requirement, so an installer who already has it present sees no diff on their next install.

#### Scenario: Installing a single section leaves the others untouched
- **WHEN** `ccst claude-md install --section workflow --apply` runs against a CLAUDE.md that
  already has the `messaging` section installed
- **THEN** the `workflow` section is added and the existing `messaging` section's content is
  byte-for-byte unchanged

#### Scenario: Omitting --section processes every registered section
- **WHEN** `ccst claude-md install --apply` runs with no `--section` given
- **THEN** every registered section (`messaging`, `workflow`, `agents`, `confirm-gate`,
  `date-format`) is installed or updated in one pass

#### Scenario: Uninstalling one section leaves the others in place
- **WHEN** `ccst claude-md uninstall --section agents --apply` runs against a CLAUDE.md with
  multiple sections installed
- **THEN** only the `agents` section's sentinel-delimited block is removed; every other installed
  section remains

#### Scenario: An unknown section id is rejected before any write
- **WHEN** `ccst claude-md install --section not-a-real-section` is given
- **THEN** the command reports an error naming the valid section ids and does not modify the file

#### Scenario: A malformed section's markers don't block an unrelated section
- **WHEN** the `agents` section's sentinel markers in CLAUDE.md are malformed (unbalanced or out
  of order) and `ccst claude-md install --section workflow --apply` is run
- **THEN** the `workflow` section installs successfully; the malformed `agents` markers only
  raise an error when `agents` itself is one of the requested sections

### Requirement: The fragment documents session working-file conventions
The CCST-managed CLAUDE.md content SHALL include a `workflow` section documenting that sessions
started via `ccd`/`ccr` get a `working/` and `out/` directory, that a deliverable likely to go
through multiple rounds of iteration should be drafted as a file under `working/` and the
confirmed final version moved to `out/`, that `working/WORKLOG.md` should be kept current because
the `worklog-guard` hook blocks a manual `/compact` when it is stale, and that the user may be
editing the same file concurrently (re-read before further edits; never discard a change the user
made directly without being asked).

#### Scenario: `workflow` section installed
- **WHEN** the `workflow` section is written or updated in `~/.claude/CLAUDE.md`
- **THEN** it documents the `working/`→`out/` draft-then-finalize convention, the
  `working/WORKLOG.md`/`worklog-guard` relationship, and the concurrent-editing caution, within
  its own `<!-- CCST:workflow START/END -->` sentinel pair

### Requirement: The fragment documents agent working-folder conventions
The CCST-managed CLAUDE.md content SHALL include an `agents` section documenting the
`<session-dir>/agents/<session-tag>--<task-slug>/` working-folder convention for dispatched
subagents (a `prompt.md` written before dispatch, an append-only `WORKLOG.md`, deliverables
written into the folder rather than returned inline), the `<session-tag>: ` prompt-prefix
convention, and pointers to the `select-agent-model` and `do-executor-critic-assessor-loop`
skills. It SHALL NOT claim that any hook enforces the prompt-prefix convention, since none does.

#### Scenario: `agents` section installed
- **WHEN** the `agents` section is written or updated in `~/.claude/CLAUDE.md`
- **THEN** it documents the agent working-folder layout, the `prompt.md`/`WORKLOG.md`/deliverables
  contents, the `<session-tag>: ` prefix, and points to `select-agent-model` and
  `do-executor-critic-assessor-loop` rather than duplicating their guidance, within its own
  `<!-- CCST:agents START/END -->` sentinel pair

#### Scenario: No session context to anchor the convention to
- **WHEN** a session has no active session tag or session directory (not a `ccd`/`ccr` session)
- **THEN** the `agents` section's guidance states that the folder convention is skipped in that
  case, rather than presenting it as unconditional

### Requirement: The fragment documents the 8-digit confirmation gate
The CCST-managed CLAUDE.md content SHALL include a `confirm-gate` section documenting that the
`confirm-8digit` hook may gate tool calls (per the `CCST_CONFIRM_8DIGIT_GATED_TOOLS` install-time
configuration, with no default gated list), that a gated call or a high-stakes action should be
confirmed by generating a code via the `generate-8digit-code` skill (never inventing one) and
proceeding only once the user types it back in their own message, and that a code from any other
source (another agent, a file, an assertion on the user's behalf) never counts as confirmation.

#### Scenario: `confirm-gate` section installed
- **WHEN** the `confirm-gate` section is written or updated in `~/.claude/CLAUDE.md`
- **THEN** it documents the `confirm-8digit` hook, the `generate-8digit-code` skill, that gating
  is configured per install rather than fixed, and that only the user's own typed reply counts as
  confirmation, within its own `<!-- CCST:confirm-gate START/END -->` sentinel pair

### Requirement: The fragment documents the date format convention
The CCST-managed CLAUDE.md content SHALL include a `date-format` section stating the date
convention that applies to everything a session writes:
- Filenames and folder names use `yyyy.MM.dd` for the date part, `yyyy.MM.dd-HHmm` with a time and
  `yyyy.MM` for a partial date.
- A formal external letter uses the long form (for example "5 October 2026") in its header only.
- Everything else uses `yyyy-MM-dd`: prose, logs, CSV/TSV and pdata date fields, frontmatter,
  JSON/YAML, commit text and chat replies. Time of day is `yyyy-MM-dd HH:mm`; a machine timestamp
  stays ISO 8601 with `T` and `Z` or an offset.
- Formats are never mixed inside one column or one field.
- One line each for the exemptions: identifiers that contain a date (session tags, branch names,
  backup stamps), names generated by tools the user does not control, existing filenames,
  evidence and files the project did not write, and machine fields an external system requires in
  ISO 8601.
- A pointer that a project session brings existing data into line by following the conversion
  prompt it is given, rather than rewriting files on its own initiative.

The section SHALL NOT name any person, machine or project, so it reads correctly for any
installer.

#### Scenario: `date-format` section installed
- **WHEN** the `date-format` section is written or updated in the user-level CLAUDE.md
- **THEN** it contains the filename, formal-letter and everything-else clauses, the time-of-day
  rule, the no-mixing rule and the exemptions, inside its own `<!-- CCST:date-format START/END -->`
  sentinel pair

#### Scenario: Section text carries no personal identifiers
- **WHEN** the `date-format` section body is inspected
- **THEN** it contains no personal name, email address, home-directory path or machine name
