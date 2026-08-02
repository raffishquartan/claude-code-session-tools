---
name: pm-pdata-schema-design
description: Use before writing a genuinely new kind of structured data into any project's ccst pdata store - deciding whether it fits an existing record_group, needs a brand-new group, and whether that group needs an extension table or free-text content suffices. Triggers on "I need to track a new kind of record", "what record_group should this be", "does this need an extension table", "add a new field to ccst pdata", or any session about to call `ccst pdata add`/`schema add-field` for a record shape it hasn't used before. Prevents lazy catch-all groups (`notes`/`misc`) that silently recreate the EAV undifferentiated-bucket problem the schema deliberately rejected.
---

# ccst pdata schema design

`ccst pdata` (spec `2026-07-26-per-project-data-store-spec.md` §4-§5) gives every project one
SQLite store with a fixed base `records` table plus optional per-`record_group` extension tables.
The schema itself never needs a CCST source change (G8) - the judgement call this skill exists for
is entirely about **how to use it well** for a new kind of data, not how to extend the tool.

## Before doing anything: check what already exists

```sh
ccst pdata schema list --project <name>
```

This lists every `record_group` already in the project's store, its row count, whether it has an
extension table, and when it was last updated. **Always run this before inventing a new group.** A
new "idea" for a group that's actually just `ccst-ideas` again, spelled differently, is the exact
failure this skill exists to prevent.

If a plausible existing group turns up, run `ccst pdata schema show --project <name> --group
<group>` to see its extension columns (with descriptions) before deciding whether the new data
fits there as-is, fits with one new field (`schema add-field`), or is different enough to warrant
its own group.

## Decision 1: does this fit an existing `record_group`?

Fits if the new items are the same *kind* of thing an existing group already holds - same rough
shape of content, same audience, same lifecycle (append-only log vs. edit-in-place current-state
vs. stacked snapshot - spec §4.3). A few new structured fields on otherwise-similar content is not
a reason for a new group; add the field instead (`ccst pdata schema add-field`).

Does **not** fit merely because two things are both "notes" or both "logs" in a generic sense -
that vagueness is exactly how a catch-all group forms. Ask: if this project's next session needed
to find these items again, would they look in the existing group's name, or would that name
mislead them?

## Decision 2: if not, what should the new group be called?

- Follow the naming convention exactly: lowercase letters, digits, hyphens only,
  `^[a-z0-9]+(-[a-z0-9]+)*$` (spec §4.2) - `ccst pdata` rejects anything else at the CLI boundary,
  so get it right the first time rather than discovering the rejection mid-migration.
- Name it for what the content **is**, specifically enough that a future session doesn't need to
  open it to know what's inside - `key-events`, `filings`, `session-output`, not `data`/`stuff`/
  `misc`/`notes`. If the best name you can find is a generic bucket word, that's a signal to look
  harder for what actually distinguishes these records, not a signal to accept the generic name.
- One content-modelling shape per group (spec §4.3): append-only log, edit-in-place current-state,
  or stacked dated snapshots. Mixing shapes in one group (some rows are a growing log, others get
  edited repeatedly) makes `update`/`list --since` behave inconsistently for callers who don't know
  which rows are which - split into two groups instead if a genuine mix shows up.

## Decision 3: does it need an extension table, or does free-text `content` suffice?

Needs an extension table (`ccst pdata schema add-field --project <name> --group <group> --field
<name>:<TYPE> [--description "..."]`) when a caller will realistically need to **query or filter**
on a specific field later - `WHERE sender = ?`, `WHERE due_date < ?`, `WHERE is_read = 0`. That's
what real typed/indexed columns are for (spec §4.3's rejection of a generic EAV table applies here
too: don't reinvent EAV by cramming structured data into `content` as a serialized blob just to
avoid a schema call).

Free-text `content` suffices when the data is genuinely prose - a decision's rationale, a
correspondence transcription, a plan document's body. Don't add a field "just in case it's useful
to filter on later" - that's speculative schema, and `schema add-field` is cheap enough to run
later, the moment an actual query need shows up (idempotent, no migration ceremony: `ALTER TABLE
ADD COLUMN`, defaults to `NULL` for existing rows, spec §4.3).

Always give `--description` when adding a field with a non-obvious meaning - it is the only home
for that information (spec §4.4: `record_group_fields` stores no type information, only prose, so
a field with a blank description is genuinely undocumented for the next session that runs `schema
show`).

## Quick reference

| Question | Where the answer comes from |
|---|---|
| What groups exist already? | `ccst pdata schema list --project <name>` |
| What fields does a group have? | `ccst pdata schema show --project <name> --group <group>` |
| Add a field to an existing group | `ccst pdata schema add-field --project <name> --group <group> --field <name>:<TYPE> --description "..."` |
| Which content-modelling shape? | append-only log / edit-in-place current-state / stacked dated snapshots (spec §4.3) |
