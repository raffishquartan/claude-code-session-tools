# `pm-project-layout-reference` — design spec

Status: Approved by Chris, with three additions (extra project-specific folders, a corrected
500-file general threshold, and a new `ccst pdata reorganize` command - design §3). Ready for
implementation planning. Source: a design conversation
(2026-08-21) that grew out of the deferred `ce30` inter-session message ("How should ccst help
set up `~/cc/<project>` consistently?", held on 2026-08-20 pending exactly this conversation)
and Chris's own follow-up questions about the two placeholder pdata-migration prompts
([[2026-08-20-pdata-migration-prompts-design]]).

## Problem

Two problems turned out to share one answer:

1. The two prompts `ccst pdata init` already points at
   (`pdata-migration-claude-md-update.md`, `pdata-migration-skills-update.md`) ship as
   placeholders - nobody has written down what a well-organised `~/cc/<project>` actually looks
   like, so there was nothing real to put in them.
2. `ce30`'s broader question - what folder conventions should `~/cc/<project>` follow, and how
   does an existing project get brought into line - was deliberately held rather than guessed at.

Chris's own proposed folder set (`correspondence/`, `meetings-and-calls/`, `analysis/`,
`workstreams/`, `workstreams-archived/`) plus a real survey of `~/cc/*` (2026-08-20/21) gives
enough grounding to answer both together, rather than solving the narrow prompt-content problem
first and redoing the work when the broader question eventually got answered anyway.

## Scope decisions (confirmed in conversation)

- **Both together**: one coherent set of conventions, both prompts derive from it - not two
  separate design efforts.
- **Advisory *detection*, no tooling enforcement**: no new `ccst doctor` check, no automatic
  flagging of when a folder has crossed the threshold - that stays a human/agent judgement call
  from documented criteria. This is a distinct question from whether *performing* a reorg once
  decided gets tooling - it does, for the one narrow flat-to-nested-split case (design §3),
  because that operation touches pdata's own data, not just files on disk.
- **Delivery**: a new bundled skill, `pm-project-layout-reference` (the canonical reference,
  mirroring `pod-reference-people`'s "read this before acting on anything X" shape, applied to
  ccst's own `pm-` skill family - `pm-project-init`, `pm-pdata-schema-design`,
  `pm-pdata-conflict-resolution`, `pm-update-central-files`), plus a new `ccst pdata reorganize`
  command that performs the one specific reorganisation the skill's criteria call for. The two
  existing prompts get real, narrowly-scoped content and reference the skill for the general
  reference rather than duplicating it.

## Design

### 1. `pm-project-layout-reference` skill

New skill under `src/cc_session_tools/skills/pm-project-layout-reference/`, shipped as ccst
package-data exactly like the other `pm-*` skills (synced to `~/.claude/skills/` via
`ccst install-everything`). Reference-only - it documents conventions and assessment criteria,
it does not perform any action itself (no scripts, no `ccst` subcommand).

Draft `SKILL.md` frontmatter (matching `pm-project-init`'s trigger-phrase style - the
implementation plan may refine the exact wording, but the shape and triggers below should carry
through):

```yaml
name: pm-project-layout-reference
description: Canonical reference for ~/cc/<project>'s optional folder conventions
  (correspondence/, meetings-and-calls/, analysis/, workstreams/, workstreams-archived/)
  and the assessment criteria for using them - which folders a given project needs, when
  correspondence/ should nest by year, and how a workstream moves from active to archived.
  Read this before setting up a new project's folders, before reorganising an existing
  project's layout, or before deciding whether a project's correspondence/ needs splitting.
  Reference-only - does not perform any reorganisation itself; for an actual flat-to-nested
  split once you've decided one is needed, see `ccst pdata reorganize`. Triggers - "how should
  this project be organised", "should correspondence/ be split", "archive this workstream",
  "/pm-project-layout-reference". Do NOT use for the pdata data-store migration itself
  (pm-project-init) or per-record-group schema design (pm-pdata-schema-design) - those are
  separate skills; this one is about folder-owned content and structure, not the data store.
```

Content:

**Folder set** (all optional - a project has whichever subset fits its nature):

| Folder | What it's for |
|---|---|
| `correspondence/` | Inbound/outbound written communication with other parties |
| `meetings-and-calls/` | Records/notes of non-written interactions |
| `analysis/` | Work product produced within the project; topic subdirectories once large |
| `workstreams/` | Active concurrent lines of work, each its own `ws-XX-<slug>` folder |
| `workstreams-archived/` | Completed workstreams, moved here intact |

**This five isn't exhaustive.** A project may have additional folders for organising its own
domain-specific records that don't fit the general five - `evidence/`, `costs/`, `filings/`,
`sources/`, a plain `data/` (already precedented: `home` and `pod` both have one for non-pdata
project data). These are project-specific by nature; standardising them is out of scope for this
skill, which documents the common core, not an exhaustive taxonomy.

Evidence this is genuinely opt-in (survey of `~/cc/*`, 2026-08-20/21, re-verified during spec
review): `deauppet` and `pbt` each have four of the five (`correspondence/`,
`meetings-and-calls/`, `analysis/`, `workstreams/`); `maxella` has three
(`correspondence/`, `meetings-and-calls/`, `analysis/` - no `workstreams/`). `home` has only
`correspondence/`; `pod` has neither `correspondence/` nor any of the other four - it's pure
`data/` (pdata store) plus docs. `biz`/`claude`/`fire`/`future`/`oneshot` have none - different
shapes entirely.

**Correction to an earlier claim in this conversation**: no project currently has
`workstreams-archived/` - not `deauppet`, not `pbt`, neither of which has ever archived a
workstream out of `workstreams/`. The `ws-XX-<slug>` naming and the `workstreams/` folder itself
*are* observed, existing practice; archiving into a separate `workstreams-archived/` folder is a
**new convention being proposed here, not yet practised anywhere** - flagged explicitly so an
implementer doesn't cite false precedent for it.

**General nesting criteria - applies to any of these folders, not just `correspondence/`:**

- Subdivide a flat folder once it passes **500 files**. For `correspondence/`, subdivide by year
  (`correspondence/2025/`), dropping to year/month (`correspondence/2025/06/`) only if a single
  year's folder would itself pass 500. For folders where content isn't naturally date-bound
  (`analysis/`, project-specific folders like `evidence/`), subdivide by topic instead once past
  the same 500-file mark.
- For a project expected to run multiple years or accumulate heavily, decide this at setup time
  rather than waiting for the flat folder to become unwieldy - retrofitting thousands of files
  later is exactly the position `deauppet` (5750 files, flat) and `maxella` (2449 files, flat)
  are in now.
- **New project, no existing volume to measure**: ask directly rather than guess - e.g.
  "Roughly how much correspondence do you expect, and over what time span - a few months, a
  year, several years, ongoing indefinitely?" - then apply the same 500-file threshold to the
  answer (translating an expected pace and duration into an expected file count). Guessing wrong
  here recreates the exact retrofit problem on a project where it was avoidable from day one.
- `pbt`'s existing `local`/`imported` split is a different axis (source provenance, not date)
  that Chris no longer recalls the rationale for - default recommendation is to fold it into
  pure date-nesting rather than preserve it as a third level, unless something surfaces during
  an actual reorg that shows it still matters.

In practice `correspondence/` is still the folder most likely to actually hit this threshold -
it accumulates one-entry-per-external-event, often at high frequency, where `analysis/` and
`meetings-and-calls/` are authored in larger, human-paced chunks and `workstreams/` is
self-limiting (bounded by how many concurrent lines of work a project realistically runs). The
criterion applies uniformly regardless - it just fires rarely for the other folders.

**Workstream lifecycle:**

- `workstreams/ws-XX-<slug>/` per active line of work, numbered sequentially
  (`ws-01`, `ws-02`, ...) - matches the existing `deauppet`/`pbt` convention.
- On completion, move the whole folder - unchanged name and number - to
  `workstreams-archived/` (new convention - see the correction above; not yet observed anywhere,
  proposed here for the first time).
- Numbering is monotonic over the project's life: a new workstream gets the next unused number
  regardless of how many have been archived, and an archived workstream keeps its original
  number forever. "ws-03" always means the same thing, active or archived.
- A non-conforming existing folder (e.g. `pbt`'s `ella-situation`) gets renamed to fit
  `ws-XX-<slug>` when convenient - advisory, not urgent.

**Relationship to `ccst pdata init`'s folder-owned/db-owned split**: these five folders hold
folder-owned content by definition - genuinely file-shaped material (attachments, PDFs, `.eml`
originals) that isn't a good fit for becoming pdata rows, per `pm-project-init`'s own default
("every markdown/text file defaults to folder-owned... db-owned when genuinely
machine-inferable"). Re-checked against real projects during spec review: `home` is
pdata-migrated *and* still has a live `correspondence/` (9 files - `.md`+`.pdf` pairs, untouched
by the migration) sitting right next to `data/` (which now holds only a non-migrated helper
script, `data/tesco/build-csv.py` - the CSVs it used to hold became pdata rows). `pod`, by
contrast, never had a `correspondence/` folder at all - it's pure `data/` plus a `CLAUDE.md`.
The two axes are orthogonal: a
project being pdata-migrated says nothing about whether it also has any of these five folders,
and vice versa. Nothing here changes when a project migrates to pdata.

**Bringing an existing project into line** (answers `ce30`'s original question directly):
*deciding* to reorganise stays a one-off, opportunistic judgement call - guided by reading this
skill, triggered by a session noticing drift or bundling it with other doc work on that project,
not scheduled or `doctor`-flagged. *Performing* a flat-to-nested split, once decided, is handled
by the new `ccst pdata reorganize` command below - not a plain manual `git mv` pass - because it
also has to keep pdata's own `file_path` references correct, which a manual move can't do safely
at scale (see design §3). Anything simpler than that (renaming one non-conforming
`workstreams/` folder, adding a brand-new folder type) stays a manual `git mv`/`mv` - the new
command is scoped specifically to the flat-to-nested split case, not general reorganisation.

### 2. The two prompts get real content

**`pdata-migration-claude-md-update.md`**: steps to find and update a project's `CLAUDE.md`
(and other top-level docs) so they describe the pdata-backed store instead of the pre-migration
flat files, plus one step: "if this project's other folders look out of step with
`pm-project-layout-reference`'s conventions, note it - don't restructure inline here.
Reorganising folders is a separate, deliberate pass, not a side effect of documenting a pdata
migration." Points at the skill for the general reference rather than duplicating it.

**`pdata-migration-skills-update.md`**: steps to find and fix project skills (under
`~/.claude/skills/`) that still read/write the pre-migration flat-file paths directly. Does
**not** reference `pm-project-layout-reference` - fixing a skill that reads a removed path is
independent of folder shape.

This keeps each prompt's own job narrow (what pdata migration specifically disrupted) while
still surfacing the general reference for awareness - the "both together" scope answer, without
turning the doc-update prompt into a full reorg tool.

**Scope of this spec vs. the implementation plan**: the two paragraphs above describe *what*
each prompt should do - they are not the prompts' final text. Writing the full, step-by-step
prompt content (matching `docs/global-claude-md-bootstrap-prompt.md`'s precedent: numbered
steps, a context-check step first, explicit idempotency notes) is implementation work, done
from this spec, not part of the spec itself. This spec resolves the *design* gate
[[2026-08-20-pdata-migration-prompts-design]]'s Task 3 was waiting on (what conventions the
prompts should assume); it does not itself take the prompts from PLACEHOLDER to finished - the
implementation plan does that.

### 3. `ccst pdata reorganize` - the flat-to-nested restructuring command

Reverses the earlier "no tooling" decision for this one specific, well-defined operation:
splitting a flat folder into a nested one (by year, or by year/month) once it's decided the 500
threshold applies. Everything else in this spec stays advisory/manual - this command exists
because a manual `git mv` pass can silently leave two other things pointing at the old flat
path: pdata records whose `file_path` names a file under the folder being moved, and other
folder-owned files (docs, notes) that mention the old path in prose. Works whether or not the
project has a pdata store at all - if it doesn't, the record-lookup step is just a no-op.

`ccst pdata reorganize --project <name> --folder <relative-path> --strategy by-year|by-year-month
[--write]`, following `ccst pdata init`'s own dry-run/`--write` shape:

- **Dry-run (default)**: for every file directly under `<project_root>/<folder>`, compute its
  new nested path (year from a leading date in the filename if the project's naming convention
  has one, e.g. `archive-correspondence`'s `YYYY.MM.DD-...` prefix, else file mtime). Query
  every pdata record in the project whose `file_path` starts with `<folder>/` and compute its
  matching new value. Grep the project's other folder-owned text files for literal occurrences
  of any old path and list them. Print the full plan - old path, new path, matched record ids,
  matched external references - for review. Nothing is moved or written.
- **`--write`**: back up the project first via the existing `backup.create_backup()` (same
  pre-mutation safety net `ccst pdata init --write` already uses - reused, not reinvented), then
  move each file (`git mv` if `project_root` is a git repo, else a plain rename), then update
  each matched pdata record's `file_path` via `service.update_record()` (version-checked,
  matching the existing optimistic-concurrency contract every other pdata write already
  follows). A version conflict or move failure partway through rolls back what's already been
  moved/updated and reports a structured failure, matching `ccst pdata init --write`'s own
  rollback contract - not a new pattern. External-reference matches are printed again as an
  explicit "still needs manual review" reminder - **never auto-edited**, since safely rewriting
  arbitrary prose without introducing a wrong edit isn't something a literal-string match alone
  can guarantee.
- Only `by-year`/`by-year-month` are in scope - not `by-topic`. A date is mechanically derivable
  per file; a topic isn't, without human judgement the tool can't safely substitute for.

## Out of scope

- Any `ccst doctor` check or other automatic *detection* of when a folder has crossed the
  threshold (the 500-file rule stays something a human/agent checks and decides to act on -
  `ccst pdata reorganize` performs a reorganisation once decided, it doesn't decide for you).
- `by-topic` restructuring, or any restructuring that isn't a flat-to-date-nested split.
- Rewriting prose references to a moved path in other files - reported, never auto-edited.
- Retroactively reorganising any specific project's folders (`deauppet`, `maxella`, `pbt`, etc.)
  - this spec documents the criteria and ships the tool; running it against a real project is
  separate, later work.
