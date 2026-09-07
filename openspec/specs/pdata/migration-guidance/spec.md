# pdata/migration-guidance Specification

## Purpose

Defines what the pdata-migration skill docs and shipped prompts must state so a session never
again completes a manual migration without discovering `ccst pdata init`, and so the pitfalls a
real migration reverse-engineered are documented rather than re-derived from scratch each time.

## Requirements

### Requirement: `pm-pdata-do-manual-migration` is deprecated in favour of `ccst pdata init`
`pm-pdata-do-manual-migration`'s `SKILL.md` SHALL state, before any other content, that `ccst pdata init` is
the tool-native migration mechanism (classification, human-reviewable manifest, backup, verify,
atomic cutover, post-write doc-update prompts) and that this skill is a deprecated fallback,
superseded by `ccst pdata init` wherever it is available.

#### Scenario: A session reads the skill before migrating
- **WHEN** a session invokes `pm-pdata-do-manual-migration` (directly or via its trigger phrases)
- **THEN** the first content it reads states plainly that `ccst pdata init` exists, is the default
  choice, and that this skill is a deprecated fallback - before any of the skill's own
  step-by-step manual-migration instructions

### Requirement: `pm-pdata-do-manual-migration` gates on explicit user confirmation before proceeding
`pm-pdata-do-manual-migration`'s `SKILL.md` SHALL instruct the invoking session to stop, after the
deprecation notice and before any manual-migration step, and obtain the user's explicit
confirmation that: (a) they understand `ccst pdata init` is the standard, tool-native mechanism and
this skill is a deprecated fallback; and (b) they intend to use this skill specifically because the
programmatic approach does not fit their case (e.g. a record shape none of `ccst pdata init`'s
import strategies can express). The skill SHALL NOT proceed to Step 1 without that confirmation.

#### Scenario: A session is invoked without the user having considered ccst pdata init
- **WHEN** a session invokes `pm-pdata-do-manual-migration` and the user has not already explicitly stated
  why the programmatic approach doesn't fit
- **THEN** the session asks the user to confirm they understand this skill is deprecated in favour
  of `ccst pdata init` and to state why the manual path is needed, and does not begin Step 1 (or
  any later step) until the user has explicitly confirmed

#### Scenario: The user confirms and the skill proceeds
- **WHEN** the user explicitly confirms they understand the deprecation and states a reason the
  programmatic approach doesn't fit
- **THEN** the session proceeds with `pm-pdata-do-manual-migration`'s existing step-by-step instructions
  exactly as before this change

### Requirement: `pm-pdata-do-audit-and-prepare-to-migrate` redirects to `ccst pdata init`
`pm-pdata-do-audit-and-prepare-to-migrate`'s `SKILL.md` SHALL state, before any other content, that
its readiness-audit output feeds `ccst pdata init` (via `pm-pdata-do-init`), not a separate manual
`pm-pdata-do-manual-migration` path, and SHALL reposition its coordination-file hygiene checks (row_id
collisions, CRLF, cross-reference integrity, controlled-vocabulary compliance) as a pre-flight pass
to run before `ccst pdata init`.

#### Scenario: A session reads the audit skill before running ccst pdata init
- **WHEN** a session invokes `pm-pdata-do-audit-and-prepare-to-migrate`
- **THEN** the skill states its output is meant to feed `ccst pdata init`/`pm-pdata-do-init`, and
  does not present `pm-pdata-do-manual-migration` as the only or default next step

### Requirement: `pm-pdata-do-init` documents the garbled-CSV-header failure mode and fix recipe
`pm-pdata-do-init`'s `SKILL.md` SHALL document, as a named caveat (mirroring
`resolve-onedrive-conflicts`' scanner-false-positive caveat pattern), that a CSV whose first row is
prose/provenance text rather than real column headers produces garbled, sentence-fragment-shaped
field names in the classification report, and SHALL document the fix recipe: confirm a real header
exists further down, strip the bad line(s) from the source while preserving the removed text,
correct the manifest entry's `fields`/`content_column`/`classification`, and record the removed
text so it survives into the cut-over entry's pointer file.

#### Scenario: A reviewer is warned before trusting a garbled classification
- **WHEN** a session following `pm-pdata-do-init` reviews a dry-run classification report containing
  a `db-owned` entry with sentence-fragment-shaped field names
- **THEN** the skill's documented caveat lets the session recognize the pattern and apply the
  documented fix recipe, rather than accepting the garbled fields or discovering the pattern by
  independent trial and error

### Requirement: `pm-pdata-do-init` documents the `file_path_column`-relative-to-a-subfolder pitfall
`pm-pdata-do-init`'s `SKILL.md` SHALL document that mapping `file_path_column` directly to a column
whose values are relative to a subfolder (not the project root) fails the write phase's
file_path-resolution verification, and SHALL document the workaround: map that column to a plain
named field instead (e.g. `source_file_path`) and note in the field's description what path it is
relative to.

#### Scenario: A reviewer hits a relative-path index file
- **WHEN** a session reviewing a proposal entry for an index-of-a-subfolder CSV considers mapping
  its path column to `file_path_column`
- **THEN** the skill's documented pitfall and workaround let the session choose the plain-field
  mapping up front, rather than discovering the verification failure at `--write` time and
  reverse-engineering the fix from `import_csv_rows`' source

### Requirement: `pm-pdata-do-init` documents the `content_column: null` default caveat
`pm-pdata-do-init`'s `SKILL.md` SHALL document that a `db-owned` entry with `content_column: null`
falls back to a raw JSON dump of the row, which is rarely the desired `ccst pdata list` output, and
SHALL instruct the reviewer to actively choose a real narrative column per entry before `--write`.

#### Scenario: A reviewer checks content_column before approving a proposal
- **WHEN** a session reviews a `db-owned` proposal entry with `content_column` left unset
- **THEN** the skill's documented caveat prompts the session to pick a real column rather than
  accepting the JSON-blob default by omission

### Requirement: `pm-pdata-do-init` documents the value-transform recipe for computed/split fields
`pm-pdata-do-init`'s `SKILL.md` SHALL document the recipe for a value transform the generic
`csv-rows` importer cannot express (a computed field such as an `is_canonical` duplicate-key flag,
or splitting one source column into two): import the file normally via `csv-rows`, then apply a
post-`--write` `schema add-field` plus a per-record `update` pass.

#### Scenario: A migration needs a computed field
- **WHEN** a session migrating a CSV needs a field with no 1:1 source column (a computed flag or a
  column split)
- **THEN** the skill's documented recipe lets the session apply the standard
  import-then-schema-add-field-then-update pattern instead of inventing an ad hoc approach

### Requirement: `pm-pdata-do-init` sets the expectation that migration is iterative
`pm-pdata-do-init`'s `SKILL.md` SHALL state that a real migration of an existing, non-trivial project
typically requires multiple `--write` rounds (as source files get fixed, deferred decisions get
resolved, and garbled entries get corrected) rather than presenting a single `--write` as the
normal terminal step.

#### Scenario: A session plans a migration for a messy existing project
- **WHEN** a session begins migrating a project with several non-trivial or ambiguous source files
- **THEN** the skill's guidance leads the session to expect and plan for multiple `--write` rounds,
  rather than treating a first `--write` as necessarily final

### Requirement: `pm-pdata-do-init` documents the two deferred-file decision criteria
`pm-pdata-do-init`'s `SKILL.md` SHALL document, as explicit review questions for every `db-owned`
proposal entry, (a) whether the file lives in a folder the project's own documentation marks as
read-only-by-convention (migrating it would move the original out of that folder, conflicting with
its documented purpose), and (b) whether the file is updated by periodic wholesale replacement
(a fresh full export dropped in) rather than incremental append (a poor migration candidate under
current tooling regardless of how clean the CSV looks, since there is no bulk-replace/upsert verb).
Both SHALL be flagged as explicit open decisions for the user, never silently resolved.

#### Scenario: A reviewer evaluates a file living in a read-only-by-convention folder
- **WHEN** a session reviews a clean, structurally-valid `db-owned` proposal entry whose file lives
  in a folder the project documents as human-only
- **THEN** the skill's documented question leads the session to flag this as an open decision
  rather than silently accepting or rejecting the classifier's proposal

#### Scenario: A reviewer evaluates a wholesale-replacement export file
- **WHEN** a session reviews a `db-owned` proposal entry for a file whose maintenance model is
  "replace wholesale with a fresh export," as opposed to incremental append
- **THEN** the skill's documented question leads the session to flag this as a poor migration
  candidate under current tooling, distinct from an append-only file with the same update
  frequency

### Requirement: The post-write prompts accept a dispatched subagent as a fresh-context substitute
`pdata-migration-claude-md-update.md` and `pdata-migration-skills-update.md` SHALL state that
running in a fresh context is the requirement, and that a dispatched `Agent` subagent from the
orchestrating session is an acceptable substitute for shelling out to a literal new `claude -p`
process.

#### Scenario: An orchestrating session runs the post-write prompts
- **WHEN** a session that just completed `ccst pdata init --write` runs the two post-write prompts
- **THEN** the prompts' own instructions permit dispatching each as an `Agent` subagent from the
  orchestrating session, without requiring a separate `claude -p` invocation
