---
name: pm-pdata-do-update-project-docs
description: Use after `ccst pdata init --write` has migrated a ~/cc/<project>'s flat files into its pdata store - rewrites the project's own top-level docs (CLAUDE.md and other top-level .md files) so they name the pdata record_group and `ccst pdata` command instead of the old CSV/JSON/flat-file path, and reports (never fixes) folder-layout drift. Safe to re-run after a later migration adds more record groups. Triggers - "update the project docs after the pdata migration", "fix the stale data references in CLAUDE.md", "/pm-pdata-do-update-project-docs"; also run automatically as a step of pm-pdata-do-init. Do NOT use for updating skills that read the old paths (pm-pdata-do-update-consuming-skills), the migration itself (pm-pdata-do-init), or reorganising folders (`ccst pdata reorganize`).
---

# pm-pdata-do-update-project-docs

Updates a project's own docs so they describe the pdata-backed store instead of the pre-migration
flat-file layout. Run it after `ccst pdata init --write` has cut a project's data over.

## Run this in a fresh context

Run this skill in a fresh context, not inline in the session that just ran `ccst pdata init
--write`. The point of a fresh read is that it cannot rationalize away a stale reference it
half-remembers writing earlier in the same conversation. Either is acceptable:

- An orchestrating session dispatches it as an `Agent` subagent. The Agent tool cannot set a cwd,
  so the subagent's prompt must begin by changing into the project root (`cd ~/cc/<project>`).
- A new Claude Code session started in `~/cc/<project>`.

Running it inline in the migrating session is not acceptable.

## Step 1 - Verify project context

Check the cwd is a `~/cc/<project>` project directory with a `CLAUDE.md`:

```sh
pwd && ls CLAUDE.md 2>/dev/null && echo "project CLAUDE.md confirmed"
```

If `CLAUDE.md` is missing, print the following and stop:

> ERROR: This skill must be run from a `~/cc/<project>` directory containing a `CLAUDE.md`.
> Aborting without touching any project docs.

## Step 2 - Read the project's docs in full

Read `CLAUDE.md` in full, plus any other top-level `.md` file in the project directory (e.g.
`README.md`, a project-specific spec or overview) - anything that might say where project data
lives. Do not read into `cc-sessions/`, `correspondence/`, or other content folders: the goal is
the project's own orientation docs, not its accumulated working history.

## Step 3 - Find references to the pre-migration flat-file layout

Look in what you read for the project's data described in flat-file terms:

- A literal path: "grocery data lives in `data/tesco/*.csv`", "see `data/orders.csv`".
- A file format with no path: "the CSV file", "the spreadsheet", "the JSON export".
- A vague folder reference: "the data folder", "raw data lives under `data/`".

Each is a candidate only if the thing it describes was actually migrated. Not every folder
reference is stale: a project can legitimately keep a `data/` folder for content deliberately left
folder-owned (`pm-project-layout-reference` §4, "Relationship to pdata": `home` is pdata-migrated
and still has a live `correspondence/` folder beside its `data/`). Cross-check each candidate
against what was migrated before deciding it is stale, using whichever is available:

- `ccst pdata list --project <name> --group <record_group>` for the groups you would expect;
- the project's `ccst-pdata-init-write.log`, for the record groups the migration created;
- `<project-root>/.pdata-migrated/`, which holds every migrated file's original, unchanged
  content under its original relative path.

## Step 4 - Update stale references

Rewrite each confirmed-stale reference to describe the pdata-backed store:

- Replace the file path with the record_group(s) now holding that data, and the `ccst pdata`
  command a reader would actually use: `ccst pdata list --project <name> --group <record_group>`
  to browse, `ccst pdata get --project <name> --id <id>` for one record, `ccst pdata query
  --project <name> --group <record_group> --where '<field> <op> <value>'` to filter.
- Name the record_group explicitly rather than saying "the pdata store" generically - a reader
  needs to know which group to query.
- Keep the surrounding sentence's purpose intact. You are changing what it points at, not
  rewriting the doc's structure or tone.

**Idempotency:** if a doc already describes its data as pdata-backed (it names a record_group
and/or a `ccst pdata` command rather than a flat-file path), leave it alone. Do not re-edit a
correct reference just to reword it. This skill is safe to re-run after a later migration adds
more record groups; on a re-run only still-stale references should change.

If Step 3 found no stale references at all, say so plainly in the report. That is a valid and
complete outcome, not a sign you missed something.

## Step 5 - Check for folder-layout drift

Read the `pm-project-layout-reference` skill and compare the project's actual folders against its
criteria. Look specifically for:

- a flat folder past the 500-file threshold that has not been split;
- a `workstreams/` folder mixing active and completed work with no `workstreams-archived/` split;
- a workstream folder name that does not fit `ws-XX-<slug>`.

**Do not restructure anything here.** Note any drift in the final report as an observation, not an
action taken. Reorganising is a separate, deliberate pass: the user runs `ccst pdata reorganize
--project <name> --folder <folder> --strategy by-year|by-year-month` (dry-run first, `--write` to
apply) when they decide to act on it.

## Step 6 - Report

Summarise for the user:

1. Each doc reference found stale and updated, quoting the before and after briefly.
2. Confirmation if nothing needed changing.
3. Any folder-layout drift from Step 5, as an observation for a separate pass.
