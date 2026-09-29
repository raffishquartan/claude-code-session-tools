## Context

Today `ccst pdata init` prints paths to two bundled prompt files (`src/cc_session_tools/prompts/`)
via `_print_migration_prompt_reminders`, backed by `_discover_prompts_dir` and tests. See proposal.md
for motivation. Bundled skills are any `skills/<name>/SKILL.md` directory; `ccst skills install`
symlinks them into `~/.claude/skills/`, so no registry needs updating. Skills here are pure
instruction skills with no scripts, so their testable surface is discovery, frontmatter, and key
content.

## Goals / Non-Goals

**Goals:**
- Future migrations get both procedures as first-class skills, invoked from `pm-pdata-do-init`.
- Preserve the prompts' substance (literal-path matching, idempotency, mechanical-rewrite rule,
  flag-don't-guess, report-only layout drift, fresh-context requirement).

**Non-Goals:**
- Running either procedure against any existing project (including `claude`).
- Changing global `CLAUDE.md`; `pm-pdata-do-init` is already the migration trigger.

## Decisions

- **Two separate skills, not sections in `pm-pdata-do-init`.** They re-run independently after later
  migrations add record groups, need a clean self-contained document for a fresh-context subagent,
  and have different blast radii (one project's docs vs. shared skills). This also matches the
  one-skill-per-procedure `pm-pdata-*` family. Alternative (sections in init) rejected: init is
  already ~240 lines and would leak migration context into the subagent.
- **Delete the prompt files and prompts/ packaging rather than keep both.** Two copies of the same
  procedure would drift. `_discover_prompts_dir` and `_print_migration_prompt_reminders` are
  replaced by a single `_print_post_migration_skill_reminders` that prints skill names, so no
  consumer reads the prompts directory.
- **Drop the `claude -p` invocation.** The skills instruct dispatching a fresh-context subagent
  with cwd at the project root (the Agent tool cannot set cwd, so the subagent prompt begins with a
  `cd`), and note that a fresh `claude` session started in the project is equivalent.
- **Tests.** Discovery + frontmatter + key-phrase assertions for both skills (each behavior the
  spec requires), a check that `pm-pdata-do-init` references both, updated CLI tests asserting the
  skill names appear and no prompt path does, and removal of prompts-directory tests.

## Risks / Trade-offs

- [Content drift while porting] -> port each step in substance and assert key phrases in tests.
- [Installed copy lags the source] -> `ccst skills install` (or reinstall) is needed on each machine;
  noted in the changelog and PR.
- [Removing prompts breaks a hidden consumer] -> grep confirmed the only consumers are `ccst.py`,
  its tests, the CHANGELOG (history only), and two archived docs (history only).
