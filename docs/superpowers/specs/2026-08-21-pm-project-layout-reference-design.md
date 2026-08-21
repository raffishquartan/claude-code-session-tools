# `pm-project-layout-reference` — design spec

Status: DRAFT, awaiting spec review + Chris's review of this file. Source: a design conversation
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
- **Advisory only, no tooling enforcement**: no new `ccst doctor` check, no thresholds enforced
  in *code*. This does not rule out writing down concrete numbers as *documented* guidance (the
  `correspondence/` nesting criteria below has one) - it rules out a linter/doctor check acting
  on them automatically. The distinction is who applies the number: a human or an agent reasoning
  from prose, not a script.
- **Delivery**: a new bundled skill, `pm-project-layout-reference`, is the single canonical
  reference (mirrors `pod-reference-people`'s "read this before acting on anything X" shape,
  applied to ccst's own `pm-` skill family - `pm-project-init`, `pm-pdata-schema-design`,
  `pm-pdata-conflict-resolution`, `pm-update-central-files`). The two existing prompts get real,
  narrowly-scoped content and reference the skill for the general reference rather than
  duplicating it.

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
  Reference-only - does not perform any reorganisation itself. Triggers - "how should this
  project be organised", "should correspondence/ be split", "archive this workstream",
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

**`correspondence/` nesting - assessment criteria:**

- Nest by year (`correspondence/2025/`) once the flat folder passes roughly **150-200 files**
  *or* spans **more than 2 calendar years**, whichever comes first. Drop to year/month
  (`correspondence/2025/06/`) only if a single year's folder would itself blow past that same
  threshold.
- For a project expected to run multiple years, decide this at setup time rather than waiting
  for the flat folder to become unwieldy - retrofitting thousands of files later is exactly the
  position `deauppet` (5750 files, flat) and `maxella` (2449 files, flat) are in now.
- **New project, no existing volume to measure**: ask directly rather than guess - e.g.
  "Roughly how much correspondence do you expect, and over what time span - a few months, a
  year, several years, ongoing indefinitely?" - then apply the same threshold to the answer.
  Guessing wrong here recreates the exact retrofit problem on a project where it was avoidable
  from day one.
- `pbt`'s existing `local`/`imported` split is a different axis (source provenance, not date)
  that Chris no longer recalls the rationale for - default recommendation is to fold it into
  pure date-nesting rather than preserve it as a third level, unless something surfaces during
  an actual reorg that shows it still matters.

**Why only `correspondence/` gets a numeric threshold**: it's the one folder that accumulates
one-entry-per-external-event, often at high frequency (every incoming/outgoing message), which is
exactly the shape that silently reaches thousands of flat files. `analysis/` and
`meetings-and-calls/` accumulate far more slowly - authored in larger, human-paced chunks - and
`workstreams/` is self-limiting (bounded by how many concurrent lines of work a project
realistically runs, and each entry is already its own dated-by-number folder, not a
one-file-per-event pile). Lighter, qualitative guidance for those three: split `analysis/` into
topic subdirectories once it holds more than a handful of clearly distinct topics (no file-count
trigger needed - it won't reach `correspondence/`-scale volume); `meetings-and-calls/` and
`workstreams/` don't need subdivision guidance at all under normal use.

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
script, `build-csv.py` - the CSVs it used to hold became pdata rows). `pod`, by contrast, never
had a `correspondence/` folder at all - it's pure `data/` plus docs. The two axes are orthogonal: a
project being pdata-migrated says nothing about whether it also has any of these five folders,
and vice versa. Nothing here changes when a project migrates to pdata.

**Bringing an existing project into line** (answers `ce30`'s original question directly): a
one-off manual/session-driven pass, guided by reading this skill first - plain `git mv`
(preserves rename history, if the project directory is a git repo) or `mv`, not a bespoke `ccst`
subcommand. Deliberately unlike `ccst pdata init`: folder reorg moves files around, it doesn't
migrate content into a data store, so it needs no transactional/rollback machinery, and building
tooling for a judgement call would be over-engineering. Trigger is opportunistic (a session
notices drift while already working in a project, or bundles it with other doc work on that
project), not scheduled or `doctor`-flagged.

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

## Out of scope

- Any `ccst doctor` check or other tooling enforcement of these conventions (explicitly decided
  against).
- A dedicated `ccst`-driven folder-reorganisation command (explicitly decided against - plain
  `git mv`/`mv` is the answer).
- Retroactively reorganising any specific project's folders (`deauppet`, `maxella`, `pbt`, etc.)
  - this spec documents the criteria; applying it to a real project is separate, later work.
