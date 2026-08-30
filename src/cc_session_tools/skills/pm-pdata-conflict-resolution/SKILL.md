---
name: pm-pdata-conflict-resolution
description: Use immediately when `ccst pdata update` or `ccst pdata delete` exits 3 (a version conflict) - takes the CLI's current-vs-attempted diff and presents it to Chris for reconciliation, per spec §6.2's "the session asks Chris how to reconcile" contract. Also use when `ccst pdata resolve` reports a cross-machine fork (a `.pdata-db-dump` conflict between two laptops), or phrasing like "pdata update conflict", "version conflict on a record", "someone else already updated this row", "sync conflict", "pdata fork between machines". Never auto-retries, never silently picks a side, never discards either version.
---

# ccst pdata conflict resolution

`ccst pdata update`/`delete` use optimistic concurrency (spec §6.2): the caller passes the
`--version` it last read, and the write only succeeds if that version still matches what's in the
store. A non-zero-rows-affected result means another session's write landed first. The CLI detects
this, exits `3`, and prints the current row's diff against what this session tried to write - this
skill is what happens next.

**Never auto-retry, never auto-merge, never silently keep one side and discard the other.** The
spec is explicit that this always surfaces to Chris - the layered defense here is structural
avoidance -> optimistic concurrency -> **a human decides**, not an increasingly clever automatic
resolution attempt (spec §6, point 2: "auto-retry or silent-log-and-skip were both rejected in
favour of always surfacing to Chris").

## When this triggers

Immediately when a `ccst pdata update`/`delete` call in the current session exits `3`. Do not wait
to be asked - a version conflict means data is at stake and the calling session should not just
move on to its next step with the write silently having failed.

## What to do

1. **Read the printed diff as-is.** The CLI already printed the current row (what's actually in
   the store now, i.e. the winning write) against the attempted change (what this session tried to
   write) - re-run with `--format json` if a structured diff is more useful than the table for the
   specific fields involved:

   ```sh
   ccst pdata get --project <name> --id <id>  # confirm current state directly if useful
   ```

2. **Present both sides to Chris plainly** - what's currently stored, what this session tried to
   change it to, and (if knowable from context - e.g. two different session transcripts, two
   different times of day) what likely produced each version. Do not editorialise about which
   version is "probably right" - that judgement belongs to Chris, who has context this session
   doesn't (which session was more authoritative, which change was more recent in wall-clock intent
   rather than just `updated_at`).

3. **Ask Chris explicitly how to reconcile.** Typical resolutions, none of them automatic:
   - Keep the current (winning) version as-is; this session's attempted change is dropped.
   - Re-apply this session's change on top of the current version - re-read the current row to get
     its fresh `version`, then re-run `ccst pdata update --version <fresh-version> ...` with
     (possibly hand-merged) content.
   - Merge specific fields from both versions into one new `update` call.
   - If the conflict reveals a genuine double-update problem (the same logical fact edited by two
     concurrent sessions because both were unaware of the other), that's worth a note back to
     whichever process let two sessions touch the same project store unknowingly - not something
     this skill fixes on its own.

4. **Never touch `--version` speculatively.** Guessing a version number to force a write through
   defeats the entire mechanism (spec §6.2) and risks silently overwriting whatever the other
   session wrote. Always re-`get` the row to learn its real current version before writing again.

## Cross-machine fork

Chris runs Claude Code on two machines (a WSL2 laptop and a MacBook) that sync `ccst pdata`
projects automatically via `.pdata-db-dump/latest.sql` (multi-laptop pdata sync design). This is a
*different* mechanism from the exit-3 case above - a fork can span every record in a project, not
one row - but resolving it lands on exactly the same principle: **never auto-retry, never
auto-merge, never silently keep one side and discard the other**, a human decides.

### When this triggers

`vector_clock.compare()` reports `FORK` - each machine made writes the other hasn't seen since
they last synced - on any of: SessionStart, SessionEnd, the hourly `ccsched` job, or a manual
`ccst pdata rehydrate`/`dump` without `--force`. The affected project shows a warning banner on
every `ccst pdata` invocation ("unresolved sync conflict - see `ccst pdata resolve --project
NAME`") until it's resolved. `ccst pdata resolve --project NAME` (Task 10) is what runs
`resolve.diff_against_dump()`/`resolve.apply_resolution()` under the hood.

### What to do

1. **Read the diff as a set of per-record choices**, not a single verdict. `diff_against_dump()`
   pairs each record's base row and extension row into one `RecordDiff` (spec's relational-
   integrity requirement - never resolve a base row and its extension row separately) and reports
   `record_group_fields` (schema-catalog) divergence as its own category, since a `schema
   add-field` run on only one machine can diverge independently of any actual data row.

2. **Present each differing record to Chris and ask local-vs-dump per record** - the same
   "never auto-retry, never auto-merge, never silently keep one side and discard the other"
   framing as the exit-3 case above applies here too, just fanned out over every record the two
   machines disagree on instead of one.

3. **Delete-vs-update conflicts need explicit framing, not a plain content diff.** When
   `RecordDiff.is_delete_vs_update` is `True`, one machine soft-deleted the record and the other
   updated it live - say so plainly ("machine X deleted this, machine Y has a live edit to it")
   rather than presenting it as if it were an ordinary two-sided content difference. Applying the
   "update" side would silently resurrect a deletion; applying the "delete" side would silently
   drop a live edit - Chris needs to see the shape of the conflict, not just two content blobs, to
   choose correctly.

4. **`RecordDiff.id_collision` is not a local-vs-dump choice at all - it's a warning that the
   `record_id` itself is meaningless for this row.** `records.id` has no `AUTOINCREMENT` (it's
   SQLite's bare rowid, assigned independently per machine from that machine's own
   `max(rowid)+1`), so two machines that fork and each insert one or more brand-new records can
   legitimately land on the *same* id for two entirely unrelated records - not a hypothetical,
   the expected outcome whenever both sides add the same number of new records to a group after
   diverging. `apply_resolution()` refuses these outright (raises rather than accepting a choice)
   specifically because picking either side would silently discard the other machine's real,
   unrelated record. Tell Chris this plainly and do not attempt to force a `local`/`dump` pick
   through `apply_resolution` for these - they need a manual, out-of-band fix (e.g. re-inserting
   the discarded record under a fresh id on whichever machine needs it), never an automatic one.

5. **`RecordDiff.group_mismatch` is the other non-choosable category, and it is *not* an id
   collision - don't describe it as one, and don't assume it's safe to fix by renaming without
   checking first.** `record_group` is mutable (`ccst pdata rename-group` rewrites it in place),
   so the ordinary cause is a group renamed on one machine only - same id, same `created_at`, two
   different group names. But `created_at` is whole-second precision and is often
   caller-supplied from a file's mtime, so two genuinely *unrelated* records independently
   inserted into different groups in the same second (or imported from files sharing an mtime)
   can coincidentally match on `created_at` too - indistinguishable from a rename by id/created_at
   alone, and the same id-collision hazard as point 4 above, just wearing a different disguise.
   Either way there is no single `ext_<group>` table a `local`/`dump` pick could write, so
   `apply_resolution()` refuses these outright. **Before treating it as a rename**, compare each
   side's `content`/`file_path` - if they describe the same real thing, it's a rename: re-run the
   same `ccst pdata rename-group` on whichever machine has not had it, so both sides agree on the
   name, then resolve again. If they describe two different things, it's a same-second id
   collision, not a rename - treat it exactly like point 4 (manual, out-of-band fix, e.g.
   re-inserting one record under a fresh id), and do NOT rename to make it match, since that would
   turn it into an ordinary content diff and let a `local`/`dump` pick silently discard one side's
   real, unrelated record.

6. **Resolution is all-or-nothing per `apply_resolution()` call.** Once Chris has decided *every*
   differing record, call `resolve.apply_resolution()` with `{record_id: "local" | "dump"}`
   covering every `record_id` in the current diff - no more and no fewer. Omitting any of them
   raises (naming the missing ids) and applies nothing. A partial resolve is not supported and is
   not safe: the vector-clock bookkeeping is per-project, not per-record, so publishing after a
   subset would declare the other machine fully incorporated while leaving records unreconciled,
   and that machine's next check would read `DUMP_DOMINATES` and overwrite its own unmerged edits
   with no prompt. If some records are blocked (point 4 or 5), the whole resolve stays blocked
   until they are fixed out-of-band - that is deliberate. The call is one atomic transaction; the
   whole resolve counts as exactly one local write for the vector clock regardless of how many
   records it touched, and the store re-publishes a fresh dump immediately after committing, so
   the other machine's next check sees a dominating fast-forward rather than a repeat of the same
   fork.

7. **A schema-catalog-only fork (no record differs, only `record_group_fields` does) blocks the
   whole resolve too, with no way to clear it via `apply_resolution()` today.** The function only
   takes per-record `local`/`dump` choices - there is no field-level equivalent yet, so it refuses
   outright (naming the differing `(record_group, field_name)` pairs) rather than either leaving
   an unresolvable dead end or silently dropping one side's field registration while publishing a
   vector that claims the dump machine is fully incorporated. Tell Chris to reconcile the schema
   catalog manually first (`ccst pdata schema add-field` on whichever machine is missing a field,
   matching the other's type/description), then retry - there is no automatic path for this yet.

8. **A checksum-invalid dump is a different failure entirely - there's nothing to diff.**
   `diff_against_dump()` raises rather than returning an empty diff in that case; the fix is
   `ccst pdata dump --force` (republish from local, the only trustworthy side), not a per-record
   resolve.

## Relationship to `ccst pdata verify`'s double-update check

`ccst pdata verify`'s suspicious-close-in-time double-update check (spec §6.3) is a *different*
mechanism catching a *different* case: two updates that both succeeded in sequence (no conflict
ever raised, because each update's `--version` matched at the moment it ran) but landed suspiciously
close together in time. That surfaces as a WARN in `ccst doctor`, not as an exit-3 conflict from
`update`/`delete` - if that's what brought you here, re-read `ccst pdata verify`'s own output
instead; there is no "current vs. attempted" diff for that case, since both writes structurally
succeeded. This skill is specifically for the exit-3 case.
