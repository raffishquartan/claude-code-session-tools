# pdata/post-migration-skills Specification

## Purpose

Defines the two skills a session runs after `ccst pdata init --write` has migrated a project, so
that the project's docs and any consuming skills describe and use the pdata-backed store rather than
the pre-migration flat files.

## Requirements

### Requirement: `pm-pdata-do-update-project-docs` updates stale flat-file references in project docs
The bundled skill `pm-pdata-do-update-project-docs` SHALL instruct a session to verify its cwd is a
`~/cc/<project>` directory containing a `CLAUDE.md` (aborting without edits otherwise), read the
project's `CLAUDE.md` and other top-level `.md` files only, and identify references to
pre-migration flat-file locations. It SHALL require each candidate to be cross-checked against what
was actually migrated (`ccst pdata list`, the init write log, or `.pdata-migrated/`) before being
treated as stale, because a project may legitimately keep folder-owned content. For each confirmed
stale reference it SHALL require the rewrite to name the record_group and the `ccst pdata`
list/get/query command, and to leave already-pdata-backed references unchanged.

#### Scenario: A doc points at a migrated flat file
- **WHEN** a project's `CLAUDE.md` says its data lives in a CSV that `ccst pdata init` migrated
- **THEN** the skill has the session replace that reference with the record_group and a
  `ccst pdata` command, keeping the surrounding sentence's purpose intact

#### Scenario: A doc references a folder that was deliberately left folder-owned
- **WHEN** a doc references a folder whose content was not migrated
- **THEN** the skill has the session leave the reference unchanged

#### Scenario: The skill is re-run
- **WHEN** every reference already names a record_group or `ccst pdata` command
- **THEN** the session makes no edits and reports that nothing needed changing

#### Scenario: Wrong working directory
- **WHEN** the cwd contains no `CLAUDE.md`
- **THEN** the session prints an error and edits nothing

### Requirement: `pm-pdata-do-update-project-docs` reports folder-layout drift without acting on it
The skill SHALL have the session compare the project's folders against `pm-project-layout-reference`
(the 500-file split threshold, active/archived workstream separation, `ws-XX-<slug>` naming) and
report any drift as an observation. It SHALL NOT restructure any folder, and SHALL name
`ccst pdata reorganize` as the separate, dry-run-first mechanism the user runs deliberately.

#### Scenario: A flat folder exceeds the threshold
- **WHEN** the project's `correspondence/` folder holds more than 500 files and is not nested
- **THEN** the final report flags it as an observation and no files are moved

### Requirement: `pm-pdata-do-update-consuming-skills` finds skills by literal old path only
The bundled skill `pm-pdata-do-update-consuming-skills` SHALL instruct a session to verify its cwd
as above, take the project name from the directory name, obtain the pre-migration paths from the
init write log, the classification report, or `.pdata-migrated/`, and search every skill's
`SKILL.md` and scripts under `~/.claude/skills/` for those literal paths. It SHALL forbid widening
the search to skills that merely sound related to the project.

#### Scenario: No skill references the old paths
- **WHEN** the literal-path search finds no matches
- **THEN** the session reports that plainly and stops

#### Scenario: A skill is related by name only
- **WHEN** a skill's name suggests the project's domain but it contains no literal old path
- **THEN** it is not treated as a match

### Requirement: `pm-pdata-do-update-consuming-skills` classifies matches and rewrites only direct I/O
For each match the skill SHALL require the session to read the surrounding code or prose and
classify it as a direct read/write or a passing mention. It SHALL have direct read/writes rewritten
narrowly to the equivalent `ccst pdata` list/get/query/add/update command, using
`ccst pdata schema show` to map fields, and SHALL have passing mentions left alone. It SHALL
require any direct match that does not map mechanically (custom locking, multi-file transactions,
non-trivial format transformation, dependence on file mtime, size, or row order) to be reported as
needing a human decision and not rewritten. Every match SHALL appear in the final report as
updated, left alone, or needing a human decision.

#### Scenario: A skill script appends a row to the old CSV
- **WHEN** a skill's script opens the migrated CSV and appends a row
- **THEN** the session rewrites that call to `ccst pdata add` for the record_group and reports it as updated

#### Scenario: A skill only mentions the old path in prose
- **WHEN** the path appears only in a comment about where data used to live
- **THEN** the skill is left unchanged and reported as left alone

#### Scenario: A skill depends on row order
- **WHEN** a skill's logic depends on the CSV's row order
- **THEN** it is not rewritten and is reported as needing a human decision

### Requirement: Both skills run in a fresh context
Each skill SHALL state that it must not run in the same context that performed the migration, and
that a dispatched `Agent` subagent whose cwd is the project root is an acceptable substitute for a
separate Claude Code session.

#### Scenario: An orchestrating session finishes a migration
- **WHEN** the session that ran `ccst pdata init --write` reaches the post-migration step
- **THEN** the skills direct it to dispatch each as a fresh-context subagent rather than run inline

### Requirement: `pm-pdata-do-init` invokes both skills after a successful write
`pm-pdata-do-init`'s `SKILL.md` SHALL include a step, after a successful `--write`, that runs
`pm-pdata-do-update-project-docs` and then `pm-pdata-do-update-consuming-skills`, each in a
fresh-context subagent.

#### Scenario: A session completes a cutover
- **WHEN** a session following `pm-pdata-do-init` reaches the end of a successful `--write`
- **THEN** the skill tells it to run the docs skill and then the consuming-skills skill

### Requirement: `ccst pdata init` output points at the skills
After a successful `--write`, `ccst pdata init` SHALL print a reminder naming both skills, and a
dry-run that produces a proposal SHALL print a reminder naming `pm-pdata-do-update-project-docs`.
Each reminder SHALL state that the skill is to be run in a fresh context (a new Claude Code session
or a dispatched subagent) whose cwd is the project root, and SHALL NOT reference any prompt file.

#### Scenario: A successful write
- **WHEN** `ccst pdata init --project demo --write` succeeds
- **THEN** stdout names `pm-pdata-do-update-project-docs` and `pm-pdata-do-update-consuming-skills`,
  includes the project root path, and ends with `SUCCESS`

#### Scenario: A dry run
- **WHEN** `ccst pdata init --project demo` produces a proposal
- **THEN** stdout names `pm-pdata-do-update-project-docs` and the project root path
