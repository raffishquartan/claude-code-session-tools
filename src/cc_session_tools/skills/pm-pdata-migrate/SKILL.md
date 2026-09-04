---
name: pm-pdata-migrate
description: Execute a project's actual migration from flat central/index files to a ccst pdata record store - build a migration plan, adversarially verify the plan itself, present it for the user's confirmation, then execute it and write a results file. Consumes the readiness manifest pm-pdata-audit produces if one exists, but always explores the project independently too, so it works even without that manifest. Triggers on "migrate this project to pdata", "run the pdata migration", "/pm-pdata-migrate", or any request to actually move a project's CSV/index/log files into pdata (as opposed to just auditing readiness, which is pm-pdata-audit, or designing the target schema in the abstract, which is pm-pdata-schema-design).
---

# Migrate a project to pdata

## When to use this vs. the other pm-pdata-* skills

- `pm-pdata-audit` - readiness audit. Finds and fixes problems in the CURRENT flat files.
  Produces a readiness manifest. Does not touch pdata at all.
- `pm-pdata-schema-design` - designs the TARGET record-group schema in the abstract (field names,
  types, groupings) before any data moves.
- `pm-pdata-migrate` (this skill) - takes whatever schema exists (designed via the skill above,
  or designed inline as part of this skill's own plan if no separate design session happened) and
  actually moves the data: `ccst pdata add`/`batch` calls, verification, and a results record.

Run `pm-pdata-audit` first if the project hasn't been through it recently - a migration
plan built against files with known unresolved duplicates or dangling rows just bakes the mess
into pdata. This skill doesn't refuse to run without that prerequisite (see below), but it will
re-derive the same checks itself if skipped, which costs more and finds less than a dedicated
audit pass would.

## Step 1: gather readiness information (works with or without a manifest)

1. Look for a readiness manifest from `pm-pdata-audit` (check the project's own
   instructions file for where it said one was written, typically `analysis/pdata-readiness.md`
   or similar). If found: read it, and check its own "STALE IF" condition - if any listed file
   has been edited more recently than the manifest's audit date, treat the manifest as a starting
   point only, not ground truth, and re-verify anything it claims about a changed file.
2. **Regardless of whether a manifest exists**, do your own independent exploration of the
   project's central/index/recordkeeping files: read the project's own instructions file in full,
   list every central file it documents, and for each one note its row/entry count, its apparent
   natural key (if any), and any obvious structural issue (multi-schema single files, inconsistent
   separators, machine-specific paths) - the same categories `pm-pdata-audit` Phase 4
   checks for. If a manifest exists, use this pass to spot-check a sample of its claims rather
   than skipping it entirely - a stale or partially-wrong manifest is worse than no manifest if
   trusted blindly.
3. Check the project's current pdata state: `ccst pdata list --project <project> --group <group>`
   for anything that may already be migrated (e.g. a `session-output` group from
   `pm-update-central-files`'s own bookkeeping is unrelated and should be left alone), and
   `ccst pdata schema` to see what record groups, if any, already exist.

## Step 2: design or confirm the target schema

For each central file being migrated, decide (or confirm, if `pm-pdata-schema-design` already
ran):

- **Record group name** - typically the file's own conceptual name (`correspondence-log`,
  `filings-index`), not its filename.
- **Natural key** - prefer a field that's already unique, stable, and won't be reassigned by a
  future session (a project-prefixed sequential id like `FIL-001` is a good sign; a per-session
  independently-renumbered `row_id` is not - see `pm-pdata-audit`'s own warnings about
  this exact failure mode, which shows up often). If nothing qualifies, mint a synthetic id
  at migration time and record the mapping.
- **Field types and normalisation** - fix the specific blockers the manifest/your own audit
  found: unify a comma-vs-semicolon tags column, normalise mixed date formats to one convention,
  split a multi-schema file (like a `#`-commented multi-section CSV) into multiple record groups
  rather than forcing it into one.
- **What does NOT migrate** - large binary source files stay on disk; pdata holds the index/
  metadata records that reference them (by relative path, never an absolute machine-specific one).

## Step 3: write the migration plan file

Before touching pdata, write a plan file (e.g. `cc-sessions/<session>/out/pdata-migration-plan.md`
or the project's equivalent working-output location) covering, per source file:

1. Source file path and row count.
2. Target record group name and field mapping (old column -> new field, 1:1 unless a
   normalisation step changes shape - state those explicitly).
3. The natural-key decision and, if synthetic, the id-generation rule.
4. Any row this migration will deliberately EXCLUDE (e.g. a row already flagged as a known data
   error awaiting the user's fix - migrating a known-bad row is worse than skipping it and noting
   why) or MERGE (two source rows collapsing into one record) - list every one by row identity,
   not just a count.
5. A rollback note: since pdata writes are individually addressable (`ccst pdata delete` per
   record), state whether this migration is safely re-runnable/reversible as designed, and if not,
   what manual step would be needed to undo it.

## Step 4: adversarially verify the plan itself (before executing anything)

Dispatch a fresh agent (or, for a small project, do this yourself with a genuinely independent
re-read) whose only job is to try to break the plan - not the eventual migrated data, the PLAN
document from step 3:

- Does every source row have an accounted-for destination (migrated, explicitly excluded, or
  explicitly merged)? Total the counts and confirm they reconcile.
- Does the natural-key choice actually hold for every row (no two source rows would collide on
  the chosen key)?
- Does any field mapping lose information (a source column with no destination field)?
- Does the plan's exclusion/merge list match what a fresh read of the source files themselves
  shows, not just what the plan claims?

Apply this loop (`do-executor-critic-assessor-loop` skill's pattern, informally: the plan-writer
is the executor, this verification pass is the critic, you or the user is the assessor) until the
plan is genuinely clean or the user accepts a documented residual risk - do not skip straight to
execution on an unverified plan.

## Step 5: present the plan summary and wait for confirmation

Give the user a concise summary (not the full plan file dump): what's migrating, row counts per
group, the natural-key choices, anything excluded/merged and why, and any residual risk the
verification pass in step 4 flagged but didn't resolve. This is a write to a shared, durable data
store - treat it with the same "confirm before hard-to-reverse actions" posture as any other
outward-facing or hard-to-reverse change. Do not proceed to step 6 without an explicit go-ahead.

## Step 6: execute

- Use `ccst pdata write_db`-equivalent batch operations (`ccst pdata add`/the batch form) rather
  than one-record-at-a-time calls where the CLI supports it, for both speed and so a partial
  failure is easier to reason about.
- After each source file's migration, immediately verify: row count in pdata matches the plan's
  expected count, and a random sample of migrated records round-trips correctly against the
  source (spot-check field values, not just presence).
- Do NOT delete or modify the original source files as part of this step, even on a fully clean
  verification - that's a separate, later decision the user makes once they've lived with the
  pdata version for a while. This skill's job ends at "the data now also lives correctly in
  pdata," not "the old files are gone."

## Step 7: write the results file

Write a results file (e.g. `cc-sessions/<session>/out/pdata-migration-results.md`) recording, per
source file: records migrated, records excluded/merged (cross-referencing the plan's own list),
verification sample results, and any discrepancy found between plan and execution. This is the
audit trail for the migration - it should let a much later session answer "why does pdata group X
have N records instead of the source file's row count" without re-deriving the reasoning.

## Common mistakes

- Refusing to run without a `pm-pdata-audit` manifest present - always fall back to your
  own exploration instead; the manifest is an optimisation, not a hard dependency.
- Trusting a present manifest without checking its staleness condition against current file
  states - a manifest from a project that's had further edits since is actively dangerous if
  taken at face value.
- Migrating a session-relative or otherwise unstable field as if it were a durable natural key -
  this is the single most common blocker `pm-pdata-audit` finds in practice; take its
  warnings about this seriously rather than assuming "it looks like an id column" is enough.
- Executing before the user has explicitly confirmed the plan summary in step 5 - a bulk pdata
  write is exactly the kind of hard-to-reverse, outward-facing action that needs confirmation
  first, not after.
- Deleting or modifying source files as part of the migration - that's out of scope for this
  skill by design (see step 6).
- Writing the results file as a bare "done" instead of a real per-file audit trail - the whole
  point of step 7 is that a much later session can reconstruct what happened without you.
