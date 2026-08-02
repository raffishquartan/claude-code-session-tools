---
name: pm-pdata-conflict-resolution
description: Use immediately when `ccst pdata update` or `ccst pdata delete` exits 3 (a version conflict) - takes the CLI's current-vs-attempted diff and presents it to Chris for reconciliation, per spec §6.2's "the session asks Chris how to reconcile" contract. Triggers on exit code 3 from either command, or phrasing like "pdata update conflict", "version conflict on a record", "someone else already updated this row". Never auto-retries, never silently picks a side, never discards either version.
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

## Relationship to `ccst pdata verify`'s double-update check

`ccst pdata verify`'s suspicious-close-in-time double-update check (spec §6.3) is a *different*
mechanism catching a *different* case: two updates that both succeeded in sequence (no conflict
ever raised, because each update's `--version` matched at the moment it ran) but landed suspiciously
close together in time. That surfaces as a WARN in `ccst doctor`, not as an exit-3 conflict from
`update`/`delete` - if that's what brought you here, re-read `ccst pdata verify`'s own output
instead; there is no "current vs. attempted" diff for that case, since both writes structurally
succeeded. This skill is specifically for the exit-3 case.
