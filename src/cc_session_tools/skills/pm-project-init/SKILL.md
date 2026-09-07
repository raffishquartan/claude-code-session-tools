---
name: pm-project-init
description: Use when initializing a brand-new project's ccst pdata store, or migrating an existing ~/cc/<project>'s informally-accumulated flat files into it - drives `ccst pdata init` and applies judgement reviewing its classification report (spec §3's folder-owned/db-owned framework), since the tool's own defaults are deliberately conservative and need a human decision on every ambiguous file. Triggers - "migrate <project> to pdata", "run pdata init for <project>", "set up the data store for <project>", "/pm-project-init". Do NOT use for ccst pdata verify, per-record-group schema design (pm-pdata-design-schema), or version-conflict reconciliation (pm-pdata-resolve-conflicts) - those are separate skills.
---

# pm-project-init

Drives `ccst pdata init` (spec §7) end to end and applies the judgement the tool itself
deliberately does not: `ccst pdata init`'s automatic classifier only proposes CSV/JSON files as
db-owned (their structure is genuinely machine-inferable); every markdown/text file defaults to
folder-owned, because guessing whether a specific project's `STATUS.md` is a log, a versioned
plan doc, or a stacked-snapshot journal is exactly the kind of per-project call this skill exists
to make - not something baked into shared tooling.

This skill's job is to **read and interpret** `ccst pdata init`'s output and drive its flags. It
never re-implements the classifier, the importer, or the verification logic itself - if the
report looks wrong, that's a bug in `ccst pdata init`, not something to work around by hand.

## 1. Rehearse first for a project's first-ever migration

For a project's very first `ccst pdata init` run - especially a high-stakes one (e.g. `pbt`,
`maxella`) - copy the project directory elsewhere first and rehearse against the copy:

```sh
cp -r ~/cc/<project> /tmp/rehearsal-<project>
ccst pdata init --project <project> --rehearse /tmp/rehearsal-<project>
```

Rehearsal is optional once the mechanism is trusted for later, lower-stakes projects, or for a
project that's already been migrated once and is just picking up a few newly-added files.

## 2. Run the dry-run classification pass

```sh
ccst pdata init --project <project> [--rehearse <path>]
```

This prints a report and writes (or, on a second run, simply re-displays) a proposal file at
`<project-root>/.pdata-migration-manifest.json` - permanent tool state, not a scratch file; a
project migrated before this filename existed may still have it under the legacy name
`.ccst-pdata-proposal.json`, which `ccst pdata` commands still read transparently. Read both.

## 3. Review every `[folder-owned]` entry that is a markdown/text file

This is the primary judgement point. For each such entry, apply spec §3's classification
framework:

- **Folder-owned, correctly defaulted:** a versioned plan document, a closed/frozen narrative
  report, a README, a principles doc. Leave it alone.
- **Should be db-owned:** an incremental note log, a current-state file edited in place, a
  stacked-dated-snapshot journal, a reference/lookup table. Flip it.

If this project already has a compiled inventory analysis (e.g. a
`per-project-data-store-inventory.md`-style document from an earlier planning session), use its
proposed `record_group`/extension-column/strategy recommendations as the starting point for the
override rather than re-deriving them from scratch.

`ccst pdata init`'s classifier auto-disambiguates two auto-classified files that would otherwise
collide on the same `record_group` (e.g. two files both named `notes.csv` in different
subdirectories) - but this only runs once, at classification time. A hand-edited override (Step 4)
that renames a `record_group` to match an existing one is **not** re-checked. Before approving the
proposal, scan it yourself for two entries sharing a `record_group` that you did not deliberately
intend to merge.

### Watch for garbled CSV headers

A CSV whose first row is actually prose/provenance text (a `#`-prefixed comment, or a multi-
sentence caveat wrapped in CSV quoting) rather than real column names produces a `[db-owned]`
entry whose proposed field names read as garbled sentence fragments (e.g.
`pending_chris_s_manual_review`, `generated_2026_08_12_do_not_edit`) rather than short domain
nouns - syntactically valid-looking identifiers that don't obviously scream "wrong" on a fast
skim. The dry-run report flags a likely case of this automatically (a "worth double-checking"
annotation on the entry's line), but always eyeball field-name **shape**, not just presence, for
every `[db-owned]` entry - the annotation is a heuristic, not a guarantee.

The content on a garbled header line is usually legitimate and valuable (provenance/caveat text a
later reader genuinely needs), not junk to discard. Fix recipe:

1. Read past the suspect first line to confirm a clean, comma-separated real header exists
   further down (usually line 2).
2. Strip the bad line(s) from the source CSV, preserving the removed text exactly - you'll need it
   in step 4.
3. Correct the manifest entry: fix `fields` to match the real header, set a real `content_column`,
   confirm `classification: db-owned`.
4. Set the entry's `preface_text` field (in `.pdata-migration-manifest.json`) to the exact text you
   removed in step 2. `--write`'s cutover step carries this into the entry's pointer file (Step 7)
   automatically, under its own heading - you don't need to compose that by hand.

### Also check every `db-owned` entry against two deferred-file criteria

Beyond field-name shape, review every `db-owned` entry (not just folder-owned markdown/text files)
against two further questions before approving the proposal - a structurally clean CSV can still
be a poor migration candidate for reasons the classifier has no way to know about:

- **Does this file live somewhere the project's own docs say only a human should touch?** (e.g. a
  `references/` folder documented as "only Chris adds/removes/reorganises files here"). Migrating
  it moves the original out of that folder as part of cutover, which conflicts with the folder's
  documented purpose. If so, flip the entry to `folder-owned` and flag it explicitly to Chris as a
  real open decision - do not silently include or silently exclude it.
- **How does this source file actually get updated going forward - incrementally appended, or
  periodically replaced wholesale with a fresh full export?** An append-only file (new rows added
  over time, however often) is a good migration candidate regardless of update frequency. A file
  whose maintenance model is "drop in a new full export periodically" is a poor candidate under
  the current tooling even if the CSV itself is clean: `ccst pdata` has no bulk-replace/upsert
  verb, so every future refresh would mean soft-deleting every old record and individually
  re-adding every new one, one CLI call per row. Flag this distinction explicitly to Chris rather
  than deciding it unilaterally - the append-vs-wholesale-replacement axis, not update frequency,
  is what actually matters here.

## 4. Hand-edit the proposal to encode overrides

Edit `.pdata-migration-manifest.json` directly (or the legacy `.ccst-pdata-proposal.json` name,
if this project still has that one). For an entry that should become db-owned:

```json
{
  "path": "planning/decisions.md",
  "classification": "db-owned",
  "reviewed": true,
  "record_group": "decisions",
  "strategy": "delimited-sections",
  "delimiter": "(?m)^## D-\\d+",
  "fields": []
}
```

Strategy choices: `whole-file` (one row, whole file), `delimited-sections` (one row per
heading-delimited section - covers both append-only logs and stacked-snapshot journals),
`csv-rows`, `json-array-rows`, `json-singleton`. Set `"reviewed": true` on entries you've made a
deliberate decision about - it has no effect on the tool's own behaviour (the proposal file is
never regenerated once it exists, reviewed or not) but keeps a human-readable record of which
entries were actually looked at versus left at their untouched default.

Two entries may legitimately feed the same `record_group`, but if they share a field name they
must give it the same `sql_type` - `--write` rejects a mismatched pair up front rather than
silently dropping one side's type.

**`file_path_column` pitfall:** don't map `file_path_column` directly to a column whose values are
relative to a subfolder rather than the project root (e.g. an index-of-a-subfolder CSV like
`references/INDEX.csv` whose own `file_path` column is relative to `references/`, not the project
root) - `--write`'s file_path-resolution verification will fail with a bare "does not resolve
under `<project_root>`" and no further guidance. Instead, map that column to a plain named field
(e.g. `source_file_path`) and note in the field's `description` what path it's relative to.

**`content_column: null` default caveat:** an entry with `content_column` left unset falls back to
a raw `json.dumps(row)` dump as every record's content - rarely what you want for readable `ccst
pdata list` output. Actively choose a real narrative column for every `db-owned` entry before
`--write`; don't accept the JSON-blob default by omission.

**Value transforms the generic importer can't express:** a computed field (e.g. an `is_canonical`
flag for a known duplicate key) or splitting one source column into two has no 1:1 mapping in
`csv-rows`. Don't try to force it into the manifest - import the entry normally via `csv-rows`
first, then apply a post-`--write` `ccst pdata schema add-field` (to add the new field) followed
by a per-record `ccst pdata update` pass (to populate it). This is the standard recipe for any
field the source file doesn't already carry as its own column.

Never delete the proposal file to "start over" without good reason - doing so discards every
override made so far. If new files appear in the project after the first dry run, add entries for
them by hand rather than deleting and reclassifying everything.

## 5. Get explicit approval before `--write`

Summarise the finished proposal in plain language for Chris - which files become which
`record_group`s, roughly how many rows each will produce, which files stay folder-owned - and get
explicit approval. **Never invoke `--write` without it.**

## 6. Run the write phase

A real migration of an existing, non-trivial project is typically **iterative, not a single
terminal `--write`**: expect multiple `--write` rounds as source files get fixed (garbled
headers), deferred decisions get resolved, and corrected entries get re-approved. Don't plan for
or promise a one-shot `--write`. A `--write` round only ever imports entries with no `migrated_at`
already set - entries already cut over by an earlier successful `--write` are skipped
automatically, so a later round picking up newly-fixed or newly-added files is safe to run without
re-touching what already migrated.

```sh
ccst pdata init --project <project> [--rehearse <path>] --write
```

`--write` streams progress to stdout as it runs and also writes everything printed - plus the
traceback of anything that escapes unhandled - to `<project-root>/ccst-pdata-init-write.log`,
flushed after every line. If a run hangs, crashes, or you're not sure what happened, read this
log rather than re-running blind. It is excluded from classification (like the proposal file
itself), so it never shows up as a manifest entry on a later dry-run.

- **Exit 0:** everything imported, verified, backed up, and cut over. Report the backup tar path
  and the list of cut-over files to Chris.
- **Exit 1:** verification failed. Nothing was cut over and no live rows were left behind (they
  were soft-deleted). Read the printed reasons - usually a bad `file_path_column`, a wrong
  `content_column`, or a header that needed a different field name - fix the proposal entry, and
  re-run `--write`.
- **Exit 2:** a CLI/validation error (e.g. no proposal file yet - re-run the dry-run first).

A failed `--write` rolls back its rows but not the schema it added first, so
`ccst pdata schema list` can show a `record_group` with zero live rows between a failed attempt
and its corrected re-run. That is expected, not data loss - the re-run reuses the schema already
in place.

## 7. After a successful cutover

Archived originals live at `<project-root>/.pdata-migrated/` and are **never auto-deleted** (spec
§7.1 step 7). `ccst doctor` will keep WARNing about them until Chris explicitly deletes them
himself - that WARN is expected and not urgent. Do not delete the archive without his explicit
instruction.

By default, `--write` also leaves a `.md` pointer file at each cut-over entry's original path
(record_group, field/schema table, an example query command, and any preserved preface text - see
Step 3's garbled-header recipe) - so anything else in the project citing the old path by name finds
a live signpost instead of a silent 404. Pass `--leave-no-pointer-files` if this run shouldn't
leave one (e.g. a rehearsal, or a project convention against extra files at migrated paths).

## 8. Rollback, if something's found wrong post-cutover

There is no `--rollback` flag. Recover by hand:

1. Restore the backup tar (printed on a successful `--write`) over the project folder:
   `tar -xzf <backup>.tar.gz -C <parent-of-project-root>`. This also drops a
   `_pdata-db/<project>.db` sibling directory alongside `<project-root>` - a snapshot of the
   project's SQLite store at backup time, CCST's own tool state, not project content. Never
   move or copy it into `<project-root>` itself; if you need to restore the `.db`, copy it to
   the real store path (`ccst pdata schema list --project <name>` will error with the current
   path if you're unsure) and leave the extracted project folder alone.
2. Undo the DB side with `ccst pdata delete --project <name> --id <id> --version <n>` for the
   specific rows that shouldn't have landed, or - for a wholesale bad run - delete the project's
   `.db` file directly and re-run `ccst pdata init` from scratch.
3. Fix whatever was wrong in the proposal (classification, strategy, field mapping) before
   re-attempting.

A genuine intentional re-migration of an entry (e.g. after restoring the backup and deciding to
redo it) also requires clearing that entry's `migrated_at` field by hand in the manifest, alongside
the DB/proposal fixes above - `--write` otherwise skips any entry that already carries one (Step 6),
by design, so it won't be re-imported until you do.

## Never do without explicit Chris approval

- Invoke `--write` on an unreviewed proposal.
- Delete anything under `.pdata-migrated/`.
- Delete a project's `.db` file as part of a rollback.
