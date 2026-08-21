# pdata migration — project doc update

This file is a self-contained prompt intended to be run after `ccst pdata init --write` has
migrated a project's flat-file data into its pdata store. It walks through updating that
project's own docs (`CLAUDE.md` and any other top-level `.md` files) so they describe the
pdata-backed store instead of the pre-migration flat-file layout.

Run it via:

```sh
cd ~/cc/<project> && \
  claude -p "Check that you are executing with a ~/cc/<project> directory as your cwd, containing a CLAUDE.md. If you are not then exit. If you are then use this file as your prompt: <path-to-cc-session-tools>/src/cc_session_tools/prompts/pdata-migration-claude-md-update.md"
```

---

## Step 1 — Verify project context

Before doing anything else, check that the current working directory is a `~/cc/<project>`
project directory with a `CLAUDE.md` present.

Run:
```sh
pwd && ls CLAUDE.md 2>/dev/null && echo "project CLAUDE.md confirmed"
```

If `CLAUDE.md` is present, proceed.
If not, print the following message and exit immediately:

> ERROR: This prompt must be run from a `~/cc/<project>` directory containing a `CLAUDE.md`.
> Aborting without touching any project docs.

---

## Step 2 — Read the project's docs in full

Read `CLAUDE.md` in full. Also read any other top-level `.md` files in the project directory
(e.g. `README.md`, a project-specific spec or overview doc) — anything that might describe where
project data lives.

Do not read into `cc-sessions/`, `correspondence/`, or other content folders at this step; the
goal is the project's own top-level orientation docs, not its accumulated working history.

---

## Step 3 — Find references to the pre-migration flat-file layout

Search what you just read for language describing the project's data in flat-file terms — from
before `ccst pdata init --write` moved that data into the SQLite-backed store. Look for phrasings
like:

- A literal path: "grocery data lives in `data/tesco/*.csv`", "see `data/orders.csv` for order
  history".
- A file-format reference with no path: "the CSV file", "the spreadsheet", "the JSON export".
- A vaguer folder reference: "the data folder", "raw data lives under `data/`".

Any of these is a candidate for update if the thing it describes was migrated into pdata. Not
every folder reference is stale — a project can legitimately still have a `data/` folder for
content that was deliberately left folder-owned (see `pm-project-layout-reference`'s §4,
"Relationship to pdata": `home` is pdata-migrated and still has a live `correspondence/` folder
untouched by the migration, sitting right next to its `data/`). Cross-check each candidate against
what actually got migrated — run
`ccst pdata list --project <name> --group <record_group>` for the record groups you'd expect, or
check the project's `ccst-pdata-init-write.log`, if one exists, for the record groups the
migration created — before deciding a reference is stale. If neither source is available,
`<project-root>/.pdata-migrated/` holds every migrated file's original, unchanged content under
its original relative path, and is worth checking too.

---

## Step 4 — Update stale references

For each reference confirmed stale in Step 3, rewrite it to describe the pdata-backed store
instead of the flat file it used to point at:

- Replace the file path with the record_group(s) that now hold that data, and the `ccst pdata`
  command a reader would actually use to see it — `ccst pdata list --project <name> --group
  <record_group>` to browse, `ccst pdata get --project <name> --id <id>` to fetch one record,
  `ccst pdata query --project <name> --group <record_group> --where '<field> <op> <value>'` to
  filter.
- Name the record_group explicitly rather than saying "the pdata store" generically — a reader
  who wants to look something up needs to know which group to query.
- Keep the surrounding sentence's original purpose intact; you're changing what it points at, not
  rewriting the doc's structure or tone.

**Idempotency note:** if a doc already describes its data as pdata-backed — already names a
record_group and/or an `ccst pdata` command rather than a flat-file path — leave it alone. Do not
re-edit a reference that's already correct just to reword it. This prompt is safe to re-run after
a later migration adds more record groups; on a re-run, only the still-stale references (if any)
should change.

If Step 3 found no stale references at all, say so plainly in your final report — that's a valid
and complete outcome, not a sign you missed something.

---

## Step 5 — Check for folder-layout drift

Read the `pm-project-layout-reference` skill (a sibling ccst-bundled skill documenting `~/cc/
<project>` folder conventions — `correspondence/`, `meetings-and-calls/`, `analysis/`,
`workstreams/`, `workstreams-archived/`, and the criteria for when a flat folder should be split
into a nested structure, e.g. by year once it passes 500 files).

Compare this project's actual folders against that skill's criteria. Look specifically for:

- A flat folder that has grown past the 500-file threshold and hasn't been split yet.
- A `workstreams/` folder mixing active and completed work with no `workstreams-archived/` split.
- A non-conforming workstream folder name that doesn't fit `ws-XX-<slug>`.

**Do not restructure anything here.** If you find drift, note it in your final summary to the
user as an observation, not an action taken. Reorganising a folder is a separate, deliberate
pass — the user runs `ccst pdata reorganize --project <name> --folder <folder> --strategy
by-year|by-year-month` (dry-run first, `--write` to apply) when they decide to act on it. This
prompt's job is the docs, not the folders.

---

## Step 6 — Report

Summarise for the user:

1. Which doc references were found stale and updated (quote the before/after briefly for each).
2. Confirmation if nothing needed changing (a valid outcome — say so plainly).
3. Any folder-layout drift noticed in Step 5, flagged as an observation for a separate pass — not
   acted on here.
