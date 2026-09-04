---
name: pm-pdata-audit
description: Full consistency/completeness audit of a project's central and recordkeeping files - OneDrive sync-conflict cleanup, cross-checking every index/register/log against what it claims to describe, and a pdata-migration readiness pass. Ends by writing a readiness manifest file that the companion `pm-pdata-migrate` skill consumes to actually run the migration. Triggers on "review all my central files", "audit the project files", "check everything is up to date and consistent", "get this project ready for pdata", "/pm-pdata-audit", or any request for a thorough review/health-check of a project's index/log/register files (as opposed to a single-session wrap-up, which is pm-update-central-files). Especially relevant when a project is scheduled to migrate to pdata - this audit is the recommended prerequisite pass, since a schema design should not be built against files that still have unresolved sync-conflict duplicates or known dangling rows.
---

# Audit central files

## When to use this vs. `pm-update-central-files`

`pm-update-central-files` is a lightweight, single-session wrap-up: rewrite WORKLOG, apply
memory rules, register new `out/` files, and surface a handful of session-specific checkpoint
items before the user exits.

`pm-pdata-audit` is a much bigger, standalone task: a project-wide sweep of every
central file (indexes, logs, registers, CSVs) that could span an entire session or more, run on
demand rather than at every session's end. Use it when the user wants the whole project's
recordkeeping health-checked, not just today's session tidied up. It is squarely a
`superpowers:dispatching-parallel-agents` case - do not attempt this audit as a single linear
pass; the agent-usage.md break-even rule ("benefits from parallel execution, OR is the kind of
work that justifies its own audit trail") applies directly.

## Overview

Four phases, each producing a durable artefact:

1. **OneDrive sync-conflict cleanup** (only if the project lives in a synced OneDrive/iCloud/etc.
   folder) - find and reconcile device-suffixed duplicate files before anything else, so later
   phases aren't confused by stale conflict copies.
2. **Parallel domain-audit agents** - one per major central-file area (however many the project
   actually has), each producing concrete, cited findings.
3. **Consolidated report + checkpoint table** - present findings to the user, categorised by how
   confidently they can be acted on, before touching anything destructive.
4. **pdata-readiness pass** - a dedicated findings file naming every blocker to a future `ccst
   pdata` migration, file by file.

Nothing destructive happens without the user's explicit go-ahead. Deletions are always handed to
the user as a reviewable `rm` script, per `resolve-onedrive-conflicts`'s convention - never run
`rm` yourself, even after approval (the harness blocks it directly in this environment; treat it
as a hard rule regardless of environment).

## Phase 1: OneDrive sync-conflict cleanup

Invoke the `resolve-onedrive-conflicts` skill and run its scanner across the whole project tree.
Read its stdout summary first - if the `true_conflict` count is disproportionately large relative
to what a manual skim would suggest, suspect a scanner false-positive before trusting the raw
number: the scanner's hyphen-splitting heuristic can misfire on a project's own multi-hyphen
naming conventions (e.g. an attachment naming pattern like `<parent>-att-N-<description>.ext`
gets matched against its own parent file as a false "sibling"). Filter the raw groups down to
genuine device-suffix conflicts (the actual device-name labels this project's synced machines
use - don't guess a generic pattern like `-PC`/`-Desktop`/`-Mac` if a substring of that pattern
can also appear inside ordinary content; verify against a handful of known real device labels
first, e.g. by grepping for the exact conflict-copy filenames a first raw pass turns up).

Split filtered conflicts into two buckets before dispatching agents in Phase 2:
- **Central/index files** (config files, registers, logs, analysis artefacts) - usually a
  handful, reviewable directly or via one dedicated agent.
- **Bulk content files** (individual correspondence/evidence/document files) - can run into the
  dozens or hundreds; dispatch as its own agent with the specific file list, not folded into a
  central-files agent.

## Phase 2: parallel domain-audit agents

List every central file/directory the project's own instructions file (CLAUDE.md or equivalent)
documents, plus anything present on disk that ISN'T documented (a documentation gap in itself).
Group into natural domains (e.g. one domain per top-level folder with its own index file) and
dispatch one Sonnet-tier agent per domain, in parallel, per `dispatching-parallel-agents` and
`select-agent-model`. Each agent's prompt should ask it to check, with concrete evidence for
every claim (not just "looks fine"):

- **File↔index correspondence**: every file the index claims to describe still exists; every
  file that should be indexed has a row.
- **Header/column/schema consistency**: every row matches the declared header; no ragged rows.
- **Controlled-vocabulary compliance**: any documented enum/pipe-separated-value column only
  uses documented values.
- **Cross-reference integrity**: any row_id/back-reference the file claims (to another index,
  another file) actually resolves.
- **"Documented but doesn't exist" gaps**: does every file/folder the project's instructions
  describe actually exist on disk? (This is a surprisingly common and durable rot pattern - a
  file gets documented once, is never built, and nobody notices for months because nothing
  breaks loudly when a CSV that's merely *referenced* is simply absent. Check this explicitly,
  don't assume documentation implies existence.)
- **Extraction/derived-artefact completeness**: if the project has a documented text-extraction
  or derived-mirror convention, spot-check or fully check that every source file has its mirror,
  and that no mirror is silently empty despite claiming success.
- **Own-documentation accuracy**: any navigation/router document (an atlas, an index-of-indexes)
  should have every specific path/count/reference it makes verified against the live filesystem,
  not just trusted.
- **pdata-migration blockers**: inconsistent separators within one column, no stable natural
  key, mixed date formats, multi-schema single files, machine-specific paths - see Phase 4.

Each agent works read-only unless a fix is unambiguous and mechanical (e.g. merging two
non-overlapping CSV logs into one with no judgement call) - anything requiring editorial
judgement is a recommendation for the consolidated report, not something an agent applies
silently. Use the `DELETE-SAFE` / `MERGE-NEEDED` / `RENAME` / `NO ACTION` verdict vocabulary from
`resolve-onedrive-conflicts` for consistency across both phases.

## Phase 3: consolidated report + checkpoint table

Once all agents report back, write one consolidated markdown report (to the session's `out/`)
covering every domain's findings, and present the user a checkpoint table split into:

- **Unambiguous fixes** ready to apply on a single approval (documentation corrections, safe
  merges already verified).
- **Genuine judgement calls** the user needs to weigh in on individually (ambiguous file
  ownership, prune-vs-keep decisions, which of two diverged forks is authoritative).
- **Real gaps** found but not yet fixed (missing extractions, undersized indexes, structural
  drift) - recommend these as follow-up work items (a project TODO.md entry, or a prompt file
  for a dedicated future session - see `writing-plans`/`subagent-driven-development` if the
  follow-up itself is large enough to need its own planning pass), not silently deferred.

Give the user a single reviewable delete script for every confirmed-safe deletion, following the
`resolve-onedrive-conflicts` convention exactly (comment header explaining each item's evidence,
`set -e`, one `rm --` per line, told to the user as "review, then run yourself").

## Phase 4: pdata-readiness pass

Compile a dedicated `pdata-readiness-notes.md` (or equivalent) listing, file by file, every
blocker a future `pm-pdata-schema-design` session would hit if it tried to design a schema
against the file as it currently stands:

- Inconsistent separator/delimiter use within one logical column.
- No column that's a safe, durable, cross-session-stable natural key (watch especially for
  `row_id`-style columns that were independently numbered by different sessions/machines - these
  are NOT stable keys even though they look like them).
- Mixed date/timestamp formats.
- A single file that's actually multiple differently-shaped tables (comment-banner lines,
  section headers, incompatible column sets under one file) - these need a "split into multiple
  record collections" design decision before migration, not a naive single-table import.
- Machine-specific or session-relative paths embedded in a column.
- Any null-value convention that isn't a real null (a string like `"NO DATA"`, an empty cell that
  means something different from a genuinely blank one).

This file is deliberately NOT a migration plan - it's a findings list for whoever runs the actual
`pm-pdata-schema-design` session next, so that work isn't designed against a still-messy
substrate.

## Phase 5: write the readiness manifest (for `pm-pdata-migrate` to consume)

Once the audit reaches a settled state (per your own stopping rule - typically two consecutive
clean verification rounds, or a documented pragmatic stop), write one machine-and-human-readable
manifest file capturing what a later migration pass needs, so it doesn't have to re-derive this
from scratch. Save it at the project's own discretion for a stable, discoverable location -
`analysis/pdata-readiness.md` or equivalent for projects with an `analysis/`-style folder,
otherwise the project's own top-level docs location. Its existence and location should also be
recorded in the project's own instructions file (`CLAUDE.md`/`AGENTS.md`/equivalent) so a future
session doesn't have to search for it.

The manifest must have, in this order, so `pm-pdata-migrate` can parse it deterministically:

1. **Header**: audit date, session/agent identifier, and an explicit "STALE IF" note - state the
   condition that would invalidate this manifest (e.g. "stale if any of the files below have
   been edited after `<date>`"), so a migration session can sanity-check freshness before trusting it.
2. **File inventory**: every central/index/recordkeeping file the audit covered, with: full path,
   row/entry count, last-verified-clean date, and its role (`index`, `register`, `log`,
   `narrative`, `derived-artefact`).
3. **Per-file migration blockers**, in the exact shape `pm-pdata-schema-design` and
   `pm-pdata-migrate` expect to consume - this is the same content this skill's own Phase 4
   already produces, just formalised into the manifest rather than a standalone prose report:
   inconsistent separators, unstable/non-existent natural keys, mixed date formats, multi-schema
   single files, machine-specific paths, null-value conventions that aren't real nulls.
4. **Recommended natural key per file**, where one exists cleanly, or an explicit "no stable key
   found, needs a synthetic id" flag where none does.
5. **Known-acceptable duplication/redundancy** - anything the audit found to be duplicate-by-
   design (a deliberately mirrored file, an index-only convenience copy) rather than an error, so
   a migration pass doesn't waste time re-flagging it or, worse, silently drops one copy.
6. **Open decisions** - anything the audit found but explicitly did NOT resolve because it needed
   the project owner's judgement (scope calls, curation completeness, which of two forks is
   canonical). List these plainly; `pm-pdata-migrate` must not treat an unresolved item as settled.

## Common mistakes

- Trusting the OneDrive-conflict scanner's raw `true_conflict` count without checking for the
  naming-convention false-positive pattern - a disproportionate count relative to a manual skim
  is a signal to investigate the pattern, not to present the raw number to the user.
- Assuming a file's presence in the project's own documentation means it exists on disk - check
  every documented path explicitly, this is one of the most common and highest-value findings.
- Running `rm` yourself after the user approves deletions - always hand back a reviewable script,
  even post-approval, matching `resolve-onedrive-conflicts`'s posture and this environment's own
  hard block on the assistant running `rm`.
- Dispatching one giant do-everything agent instead of one agent per domain - domain audits are
  naturally independent and should run in parallel; a single sequential pass wastes wall-clock
  time for no accuracy benefit.
- Skipping the pdata-readiness pass because "the project isn't migrating yet" - if the project is
  even loosely on a path toward pdata (check for an existing `.pdata-db-dump/` or a mention of
  pdata migration in the project's own instructions/TODO), this pass earns its cost even before a
  schema-design session is scheduled, since the findings inform how urgently the cleanup itself
  should happen.
- Silently fixing a genuine judgement call an agent flagged as ambiguous, instead of surfacing it
  to the user - the whole point of the checkpoint-table split in Phase 3 is that "safe to apply"
  and "needs a human decision" are not the same bucket, even when both are technically fixable.
