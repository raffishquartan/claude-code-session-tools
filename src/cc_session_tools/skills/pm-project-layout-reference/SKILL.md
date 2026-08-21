---
name: pm-project-layout-reference
description: Canonical reference for ~/cc/<project>'s optional folder conventions (correspondence/, meetings-and-calls/, analysis/, workstreams/, workstreams-archived/) and the assessment criteria for using them - which folders a given project needs, when correspondence/ should nest by year, and how a workstream moves from active to archived. Read this before setting up a new project's folders, before reorganising an existing project's layout, or before deciding whether a project's correspondence/ needs splitting. Reference-only - does not perform any reorganisation itself; for an actual flat-to-nested split once you've decided one is needed, see `ccst pdata reorganize`. Triggers - "how should this project be organised", "should correspondence/ be split", "archive this workstream", "/pm-project-layout-reference". Do NOT use for the pdata data-store migration itself (pm-project-init) or per-record-group schema design (pm-pdata-schema-design) - those are separate skills; this one is about folder-owned content and structure, not the data store.
---

# pm-project-layout-reference

This is the canonical reference for `~/cc/<project>`'s optional folder conventions. Read it
before setting up a new project's folders, before reorganising an existing project's layout, or
before deciding whether a folder needs splitting.

## 1. The five common folders

| Folder | What it's for |
|---|---|
| `correspondence/` | Inbound/outbound written communication with other parties |
| `meetings-and-calls/` | Records/notes of non-written interactions |
| `analysis/` | Work product produced within the project; topic subdirectories once large |
| `workstreams/` | Active concurrent lines of work, each its own `ws-XX-<slug>` folder |
| `workstreams-archived/` | Completed workstreams, moved here intact |

This five isn't exhaustive. A project may have additional folders for organising its own
domain-specific records that don't fit the general five - `evidence/`, `costs/`, `filings/`,
`sources/`, a plain `data/` (already precedented: `home` and `pod` both have one for non-pdata
project data). These are project-specific by nature; standardising them is out of scope for this
skill, which documents the common core, not an exhaustive taxonomy.

Not every project needs all five. Decide which apply by asking what kinds of records the project
actually accumulates - a project with no external correspondence doesn't need `correspondence/`;
a project with no concurrent lines of work doesn't need `workstreams/`.

## 2. Nesting criteria

These criteria apply to any of the folders above, not just `correspondence/`:

- Subdivide a flat folder once it passes **500 files**. For `correspondence/`, subdivide by year
  (`correspondence/2025/`), dropping to year/month (`correspondence/2025/06/`) only if a single
  year's folder would itself pass 500. For folders where content isn't naturally date-bound
  (`analysis/`, project-specific folders like `evidence/`), subdivide by topic instead once past
  the same 500-file mark.
- For a project expected to run multiple years or accumulate heavily, decide this at setup time
  rather than waiting for the flat folder to become unwieldy.
- **New project, no existing volume to measure:** ask directly rather than guess - e.g. "Roughly
  how much correspondence do you expect, and over what time span - a few months, a year, several
  years, ongoing indefinitely?" - then apply the same 500-file threshold to the answer.
- A project's existing `local`/`imported` (or similar source-provenance) split, if one exists, is
  a different axis from date - fold it into pure date-nesting rather than preserve it as a third
  level unless something surfaces during an actual reorg that shows it still matters.

## 3. Workstream lifecycle

- `workstreams/ws-XX-<slug>/` per active line of work, numbered sequentially (`ws-01`, `ws-02`,
  ...).
- On completion, move the whole folder - unchanged name and number - to
  `workstreams-archived/`. No project currently has `workstreams-archived/` yet - this is a new
  convention, not yet observed anywhere. Don't cite it as existing practice.
- Numbering is monotonic over the project's life: a new workstream gets the next unused number
  regardless of how many have been archived, and an archived workstream keeps its original
  number forever.
- A non-conforming existing folder gets renamed to fit `ws-XX-<slug>` when convenient - advisory,
  not urgent.

## 4. Relationship to pdata

These five folders hold folder-owned content by definition - genuinely file-shaped material
(attachments, PDFs, `.eml` originals) that isn't a good fit for becoming pdata rows. A project
being pdata-migrated (see `pm-project-init`) says nothing about whether it also has any of these
five folders, and vice versa - they're orthogonal. `home` is the concrete example: it's
pdata-migrated (its structured data now lives in SQLite) and still has a live `correspondence/`
folder full of `.md`+`.pdf` pairs, untouched by the migration, sitting right next to its `data/`
folder. `pod`, by contrast, never had a `correspondence/` at all - it's pure `data/` plus docs.

## 5. Performing an actual reorganisation

To actually perform a flat-to-nested split by date, use `ccst pdata reorganize --project <name>
--folder <folder> --strategy by-year` (or `--strategy by-year-month` for higher-volume folders) -
dry-run first, `--write` to apply. See its `--help` for details.

The tool only automates date-based splitting - it's the mechanical case, since a date is
derivable per file. Topic-based splitting (`analysis/`, or any project-specific folder without a
natural date) has no tool support and stays a manual `git mv`/`mv` pass, since grouping by topic
needs human judgement the tool can't safely substitute for. Anything simpler than a split
(renaming one folder, adding a new folder type) is also a plain `git mv`/`mv`.
