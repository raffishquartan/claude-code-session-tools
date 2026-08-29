# Multi-laptop `ccst pdata` sync (automatic) — design spec

Status: DRAFT, awaiting review. Supersedes the 2026-08-02 manual-push/pull spec (source:
`cc-sessions/20260802-claude-make-prompt-to-make-ccst-multi-laptop/working/multi-laptop-sync-spec.md`;
plan docs: `docs/superpowers/plans/2026-08-02-multi-laptop-sync-{00-overview,01-core-sync,
02-foreign-session-detection,03-backup-state}.md`) — see "Relationship to the 2026-08-02 spec"
below for what carries forward and what's explicitly reversed.

## Problem

`~/.local/share/claude/project-db/<project>.db` (the `ccst pdata` per-project store) is
deliberately kept outside the OneDrive-synced project folder (`lib/paths.py`'s own docstring, on
`data_home()`/Root B: "isn't safe to assume machine-portable... Never assumed to sync across
machines"). Chris runs Claude Code on two machines (a WSL2 laptop and a MacBook) and alternates
between them without a strict canonical machine, so each machine's pdata for a given project can
diverge with nothing to reconcile it.

## Goals

1. Keep each of the 10 `~/cc`-rooted projects' pdata in sync across both machines, automatically,
   with no manual step required for the common case.
2. Never silently lose a write on either machine — a genuine conflict (concurrent edits on both
   machines since they last synced) must always be surfaced to Chris, never auto-merged or
   auto-discarded.
3. Tolerate OneDrive's eventually-consistent, delayed, sometimes-out-of-order delivery without
   misordering updates or mistaking staleness for currency.
4. Never corrupt or data-race a live `.db` file, including when the automatic triggers fire while
   Claude Code (or another `ccst pdata` invocation) is actively using it.

## Non-goals

- Cross-platform session **resume** (transcripts, tag files) — out of scope, unchanged from the
  2026-08-02 spec's §3.
- Cross-machine `ccmsg` or any other CCST-infra store (`sessions.db`, `ccsched.db`,
  `telemetry.db`, `command-cache.db`) — machine-local only, unchanged from 2026-08-02 spec §4.1.
- Syncing the 19 `~/repos`-rooted `session-output`/`session-output-watermark` stores — confirmed
  during this session's investigation to be an unrelated, auto-reconciled, fully re-derivable
  index (see `ccst pdata reconcile-session-output`), not authored data. Tracked separately as its
  own fix (scope the reconciliation job to `CLAUDE_SESSION_TOOLS_PROJ_ROOT` only) - not part of
  this spec.
- **Reversed from 2026-02-02 spec:** "no daemon, no automatic/continuous sync" is no longer a
  non-goal - see below.

## Scope

The 10 `~/cc`-rooted pdata projects only (`biz, claude, deauppet, fire, future, home, maxella,
oneshot, pbt, pod`, plus any added later under `CCST_PROJECTS_ROOT`).

## Storage layout

- `.pdata-db-dump/` at each project's root (`~/cc/<project>/.pdata-db-dump/`) - rides OneDrive's
  existing sync of the project folder, nothing new to configure.
- Contains: `latest.sql` (the current dump, deterministic text - see "Dump format"),
  `latest.sha256` (checksum of `latest.sql`), and `archive/<timestamp>.sql` for the last 24
  dumps (pure history, never read by any automatic check - browsed/restored manually only).
- **`project_db_dir()` itself is untouched by this spec.** This session's investigation also
  surfaced a latent namespace-collision risk shared by the 10 in-scope projects and the 19
  unrelated `~/repos`-rooted `session-output` stores (both resolve through the same flat
  `project_db_dir()`/`db_path()`) - but fixing that means touching code the session-output
  reconciliation fix already owns, and has its own migration needs for ~29 existing files on
  disk. Deliberately left out of this spec (see "Open items") rather than half-adopted here.
  **Confirmed not a blocker for this spec specifically:** this spec only ever synchronises the 10
  `~/cc`-rooted projects - anything inside a `~/repos` dev repo is assumed to move machine-to-
  machine via git, never via this mechanism. The collision risk is about two different local
  `.db` files accidentally becoming one on a single machine (a `~/cc` project and a same-named
  `~/repos` repo both resolving to the same `project-db/<name>.db`), not about cross-machine sync
  - real, but independent of, and not created or worsened by, anything in this spec. It's also
  now lower-probability in practice than when first flagged: the `reconcile-session-output`
  (2.11.1) and `pm-update-central-files` (2.11.2) fixes mean neither of the two automatic paths
  that used to write session-output rows for a `~/repos` project will do so any more - the
  remaining risk is a human explicitly creating a same-named project under both roots, not
  something either automatic mechanism can trigger today.

### Machine identity

`pdata_meta.machine_id` (below) needs a stable, human-distinguishable value - reused unchanged
from the superseded 2026-08-02 spec (§4.2): hostname auto-detected as the default; the first time
this tooling runs on a given laptop it prompts once to confirm or override, then stores the
confirmed value locally (`CCST_MACHINE_NAME` env var convention, or an equivalent small local
config file - not synced) so it survives a later hostname change. No new *design* needed here -
`lib/machine_identity.py` is specified (not yet built) by the prior spec's Plan 2; implement it
from that design rather than re-deriving it, but it does not exist on disk yet.

**Hostname-collision check, new - a plain hostname is not guaranteed unique** (a freshly-imaged
corporate laptop or a default "MacBook-Pro" is a real collision risk, independent of whether
Chris's own two machines happen to collide today). The confirm-once prompt (above) checks the
proposed name against every `machine_id` already known to *this project* before accepting it -
specifically, against any project where a local `.db` already exists (its own `pdata_meta`) and,
for a project with no local `.db` yet (see "adopt-from-dump" below), against whatever
`.pdata-db-dump/latest.sql` on disk already shows. Two outcomes:

- Proposed name not seen anywhere → confirm and store, as today.
- Proposed name already exists as a *different* machine's entry in some project's vector → this
  cannot be resolved automatically (software cannot tell "this is the same physical machine
  reconnecting after losing local state" apart from "this is a genuine hostname collision with a
  different machine") - surface both possibilities plainly and require an explicit choice: confirm
  anyway (same machine, continuing), or enter a different name now (collision, pick something
  distinguishing). Never silently proceed with a name already in use by an unconfirmed "is this
  really you" state.

## The vector clock (`pdata_meta` table)

New table inside every in-scope project's `.db`:

```sql
CREATE TABLE IF NOT EXISTS pdata_meta (
    machine_id TEXT PRIMARY KEY,
    revision   INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
```

One row per machine this DB has ever heard from, including itself. This table **is** the local
machine's current vector clock - generalises cleanly to any number of machines (not just two).

**Binding invariants** (both required for correctness, neither optional):

1. **Atomicity.** Every local pdata write (record add/update/delete, schema change) increments the
   local machine's own `revision` in the *same transaction* as the data change - never a separate,
   later bump. Every rehydrate replaces the *entire* DB (data tables and `pdata_meta` together) in
   one atomic operation - never an in-place partial merge. If content and vector can ever drift
   apart, every comparison below becomes untrustworthy.
2. **Full-vector propagation.** Every dump embeds the *entire* `pdata_meta` table as it stood at
   dump time, not just the dumping machine's own counter. This is what makes transitive knowledge
   ("machine C already incorporated machine A's change via machine B") detectable without every
   pair of machines having synced directly.

**Comparison rule**, given the local DB's vector `V_db` and an incoming dump's vector `V_dump`:

- `V_dump` **dominates** `V_db` (≥ on every machine, > on at least one) → clean fast-forward: safe
  to rehydrate.
- `V_db` dominates `V_dump` → the dump is already stale/incorporated; no-op.
- **Neither dominates** (each has a revision the other lacks) → genuine fork. Never auto-resolved.

## Dump format

Deterministic text, **not** `sqlite3.Connection.iterdump()` used as-is, and not a raw binary copy
of the `.db` file. Binary is ruled out because SQLite's on-disk byte layout isn't deterministic
for identical logical content (page ordering, vacuum state), so a binary checksum would falsely
flag unchanged content as changed. `iterdump()` was tested directly (three scripted checks against
this exact schema, reproducible) rather than assumed, and turned out to be *only partially*
deterministic:

- Tables and indices are emitted in a fixed, content-only order (confirmed: alphabetical by name,
  not creation order) - safe regardless of each machine's own schema-evolution history.
- A table whose primary key is a bare `INTEGER PRIMARY KEY` (true of `records` and every
  `ext_<record_group>` table, which key on `record_id INTEGER PRIMARY KEY REFERENCES
  records(id)`) dumps in ascending key order regardless of insertion order, because that column
  *is* the table's rowid - confirmed by inserting the same three logical rows in reverse order
  across two separate databases and diffing their dumps byte-for-byte (identical).
- **A table with a composite or non-integer primary key does not** - `record_group_fields`
  (`PRIMARY KEY (record_group, field_name)`) dumped in *insertion* order, not key order, in the
  same test - two databases holding the identical three rows, inserted in a different sequence on
  each, produced different dump bytes. This would make the dump falsely appear to change (or fail
  to change) across machines whose schema/field-history diverged, for reasons having nothing to
  do with their actual content.
- No PRAGMA or other file-level setting (page size, encoding, journal mode) appears in `iterdump()`
  output in any of these tests - confirmed, not assumed.

Because of the composite-PK finding, the dump routine is a **thin custom wrapper**, not raw
`iterdump()`: enumerate tables/indices from `sqlite_master` sorted by name (matching the safe
behaviour observed, but made explicit rather than relied upon as undocumented), and for each
table's rows run `SELECT * FROM "<table>" ORDER BY <that table's primary-key columns>` rather than
`iterdump()`'s unordered per-table fetch - giving every table the same "ordered by content, not by
history" guarantee the `INTEGER PRIMARY KEY` tables already get for free. `sqlite3` (not a system
dependency - this machine has no `sqlite3` CLI binary installed, confirmed) is never shelled out
to; everything here is the Python stdlib `sqlite3` module.

Each dump file embeds: `machine_id`, the full `pdata_meta` vector, `dumped_at`, and is paired with
a `.sha256` checksum of its own content - a truncated/corrupted dump (e.g. caught mid-OneDrive
upload) is detected as a checksum failure, distinct from a genuine fork (see "Conflict handling").

**Required test, not optional** (ties to the empirical checks above): an automated test must
create two independently-built databases holding identical logical content via different
insertion/schema-evolution histories (mirroring the manual checks just run) and assert their dumps
are byte-identical - this is what actually proves determinism, not a docstring claim.

## Triggers

| Trigger | Direction | Condition to act |
|---|---|---|
| SessionStart (this project) | Rehydrate only | `latest.sql`'s checksum validates **and** its vector dominates `V_db` → rehydrate, then immediately re-dump (publishes the merged state right away, see below). Checksum failure or fork → abort, surface (see below); DB is left untouched, session continues normally. |
| SessionEnd (this project) | Dump only | `V_db[self] > V_dump[self]` **and** `V_dump[k] == V_db[k]` for every other machine `k` (dump isn't behind on anything local already knows) → write a fresh dump. If the dump shows un-incorporated foreign updates instead, or fails its own checksum, abort the dump and surface a conflict (below) rather than publish over it. |
| Hourly `ccsched` job | Both, in order | (1) Rehydrate-check exactly as SessionStart's rule - safe here because cron runs with no live session, so none of SessionStart's process-gate concern applies. (2) If no rehydrate happened, dump-check exactly as SessionEnd's rule. (3) Otherwise no-op, or surface a fork. |
| `ccst pdata rehydrate [--project NAME \| --all] [--force]` | Rehydrate only | Same rule as SessionStart without `--force`. With `--force`: adopt the dump's content regardless, discarding local's unpublished writes ("dump wins"). |
| `ccst pdata dump [--project NAME \| --all] [--force]` | Dump only | Same rule as SessionEnd without `--force`. With `--force`: overwrite the dump with local's state regardless ("local wins"). |
| `ccst pdata resolve --project NAME` | Either | Interactive, per-record diff between local DB and the dump (reusing the existing `pm-pdata-conflict-resolution` "current vs. attempted" display, sourced from local-vs-dump instead of local-vs-winning-write) - lets Chris pick a side or hand-merge per record instead of blunt-overwriting one side wholesale. |

Rehydrating always immediately re-dumps the merged state. This is why the dump-trigger's equality
condition (`V_dump[k] == V_db[k]` for every other `k`) holds in practice without extra
bookkeeping: the harder case (local learned something new about a remote machine) is resolved
synchronously by the rehydrate step, so the periodic dump-check only ever has to handle the simple
case (local made new writes of its own).

## Process safety

The 2026-08-02 spec's running-session gate ("no `claude` process running anywhere on the machine")
cannot work for automatic triggers - a SessionStart hook only ever runs from inside a live
`claude` process, and cron/SessionEnd firing while Claude Code is open and idle shouldn't be
blocked just because a `claude` process exists somewhere. Replaced with, scoped to the specific DB
rather than the whole machine:

- **Dumping** needs no gate - it's a read-only copy (SQLite's own dump mechanism), which SQLite
  already makes safe against concurrent access.
- **Rehydrating** (the risky, content-replacing direction) attempts a non-blocking exclusive check
  against the local `.db` (`BEGIN IMMEDIATE` with a near-zero busy-timeout) immediately before the
  swap; if another writer holds it right now, skip this attempt and let the next trigger retry.
  The swap itself writes to a fresh temp file, verifies its checksum, then atomically
  `os.replace()`s it over the live path - matching the atomicity invariant above.
- **The hourly cron job additionally skips rehydrating a project if a live Claude Code session is
  currently working in it** - a layer above the lock check, about not surprising a live session by
  changing its data mid-task even when no write happens to coincide with the exact rehydrate
  instant. (SessionStart is unaffected - it only ever rehydrates before *this* session has done
  anything, so there is nothing to surprise.) Tested directly, not assumed: `pgrep -x claude` lists
  every running `claude` process by PID; on Linux/WSL2, `readlink /proc/<pid>/cwd` resolves that
  process's actual working directory with no ambiguity - verified against this session's own
  `claude` process just now, which resolved to exactly this project's root, confirming the PID
  found by `pgrep` really does map to a real, comparable project directory. A project is
  "occupied" if any `claude` process's resolved cwd equals that project's root exactly (same
  equality test `roots.matched_session_root()` already uses). macOS has no `/proc` - the
  equivalent there is parsing `lsof -a -p <pid> -d cwd -Fn` for the `n`-prefixed path line; this
  needs confirming on the Mac specifically during implementation, since it could only be tested on
  the WSL2 side this session. If the check cannot be performed at all (neither mechanism
  available, or a permission error) the project is treated as occupied - fail safe, never fail
  open - and the cron tick simply retries next hour.

## Conflict handling & notification

**Two distinct failure modes, distinct messaging:**

- **Checksum failure** (dump corrupt/truncated) - nothing reliable to diff; the message states
  this plainly and the fix is `ccst pdata dump --force` (republish from local, since local is the
  only trustworthy side).
- **Genuine fork** - the dump is readable; there's a real, showable diff. The message names which
  machine(s) diverged and points at `ccst pdata resolve` (careful, per-record) or `rehydrate
  --force` / `dump --force` (blunt, pick a side).

**Both failure modes, on every trigger that can hit them** (SessionStart, SessionEnd, hourly
cron, manual `dump`/`rehydrate` without `--force`):

- A Telegram message via the existing `notify-user` skill's channel (no new integration).
- A line in the existing SessionStart additional-context digest (the same mechanism already
  surfacing cc-scheduler catch-up and pending-rename notices) the next time a session starts in
  that project - no new plumbing, just another digest source.

**While a conflict is open:** ordinary `ccst pdata` reads/writes for that project continue to
work - every invocation prints a short warning banner ("unresolved sync conflict - see `ccst
pdata resolve --project NAME`") but is never blocked. Blocking real work over an open sync
conflict was considered and rejected - the warning is the deterrent, not a hard stop.

**Resolution must preserve relational integrity, not just pick a side per record.** The schema
has real structure `ccst pdata resolve` cannot ignore:

- Every `ext_<record_group>` row is 1:1 with a `records` row via `record_id INTEGER PRIMARY KEY
  REFERENCES records(id)` - resolving a record means resolving its base row *and* its extension
  row together, atomically, in one transaction. Taking one side's base row with the other side's
  extension row is corruption, not a valid outcome.
- `record_group_fields` (the schema catalog) can itself diverge independently of any data row - a
  `schema add-field` run on one machine and not the other. Diffing must include schema-catalog
  rows as their own category, not only `records`/`ext_*` content, and a record can only be
  considered "resolved" once its `record_group`'s schema is reconciled on the side that adopts it
  (an extension column present in the source but missing on the target gets added via the
  existing `ALTER TABLE ADD COLUMN` path *before* that row is inserted) - resolving a record must
  never silently drop a field present in the schema one side already knows about.
- A delete-vs-update conflict (one machine soft-deleted a record via `deleted_at`, the other
  updated the same record) is its own case, not reducible to "diff the content" - `ccst pdata
  resolve` must present it explicitly as delete-vs-update, never silently resurrect a deleted
  record by applying an update over it, nor silently drop an update by keeping the delete.

**Conflict-resolution documentation:** extend the existing `pm-pdata-conflict-resolution` skill
with a second section for this cross-machine case, alongside its existing single-file
optimistic-concurrency section - one skill owns "a pdata conflict was detected, here's the
protocol" regardless of which of the two mechanisms caught it.

## `ccst pdata init` on a second machine (adopt-from-dump)

A project migrated to pdata on laptop A has never had `ccst pdata init` run on laptop B - each
machine's `project-db/<project>.db` is independent (Storage layout, above), so laptop B has no
local DB for it yet. Today, `ccst pdata init --project NAME` only knows how to build a fresh
store by classifying and importing the project's flat files (spec referenced throughout the
codebase as §7.1) - run unmodified on laptop B, it would re-import the project's *current* files
as if this were its first-ever migration, producing a DB disconnected from laptop A's vector
clock entirely (no shared history, `pdata_meta` starts empty) and likely duplicate content
already captured by laptop A's store.

`ccst pdata init` therefore gains an **adopt-from-dump** fast path, checked before the normal
classify/import flow: if `<project>/.pdata-db-dump/latest.sql` exists and its checksum validates,
initialise the new local DB by rehydrating from it directly (same mechanism as the regular
rehydrate trigger) instead of running classification/import at all. The new machine registers
itself in the adopted `pdata_meta` with revision `0` (it has made no writes yet) - the same "new
machine joins with a zero row" shape as the `pdata_meta` walkthrough in "The vector clock" above.
Console output distinguishes this explicitly from a fresh migration (e.g. "Adopting existing
pdata from sync dump (published by <machine>, <timestamp>) - skipping file classification/import"
vs. the normal flow's classification report). If the dump exists but fails its checksum, `init`
fails with the same corrupt-dump guidance as any other rehydrate attempt (see "Conflict
handling") rather than silently falling through to a fresh import.

**This is not only a manual-`init` concern.** "No local `.db` yet, but a dump exists" is simply
the limiting case of the rehydrate comparison in "Triggers" below - an absent local vector is
trivially dominated by any dump's vector. SessionStart and the hourly cron job therefore both
handle it automatically too, via the same adopt-from-dump path: a project with no local `.db` that
has a valid `.pdata-db-dump/latest.sql` gets its local store created and populated by SessionStart
the first time a session opens there, or by the next hourly cron tick, with no manual `ccst pdata
init` required - `init`'s adopt-from-dump path exists for the deliberate/explicit case (setting up
a new machine before ever starting a session there), but isn't the only way it happens. The
machine-identity confirm-once prompt (above) still runs as part of whichever path first
establishes this machine's presence in that project.

## Manual end-to-end verification (post-implementation, using `home`)

`home` is already pdata-migrated on this laptop and not yet set up on the other. This sequence
exercises every trigger in the design against a real project, across both machines, and must be
run (by Chris) after implementation, before considering this feature done - none of it is
automatable from inside a single Claude Code session, since the whole point is crossing machines.

1. **Dump works here.** Run `ccst pdata dump --project home --force`. Confirm
   `~/cc/home/.pdata-db-dump/latest.sql` and `.sha256` exist, `latest.sql`'s embedded `machine_id`
   is this laptop, and its vector shows this laptop's current revision with every other known
   machine at `0`. Wait for OneDrive to finish propagating the folder to the other laptop before
   continuing (check the file's presence/mtime there).
2. **Adopt-from-dump on the other laptop.** There, run `ccst pdata init --project home`. Expect
   the adopt-from-dump path (above) to fire - console output names it explicitly, not the normal
   classification report. Confirm `ccst pdata verify --project home` passes there, and a
   spot-checked record matches this laptop's copy exactly.
3. **Touch the DB on the other laptop** - any `ccst pdata add`/`update` against `home`.
4. **Publish from the other laptop** - trigger a dump there (`ccst pdata dump --project home` or
   wait for its SessionEnd/hourly-cron trigger). Wait for OneDrive to propagate back here.
5. **Watch it rehydrate here.** Start a Claude Code session in `home` on this laptop (or run
   `ccst pdata rehydrate --project home` directly for a faster loop). Confirm the SessionStart
   digest names the rehydration explicitly (machine + timestamp it rehydrated from), and that
   step 3's change is now visible in this laptop's local DB.
6. **Touch the DB here** - any `ccst pdata add`/`update` against `home`, on this laptop.
7. **Publish from here** - trigger a dump (`ccst pdata dump --project home` or its natural
   trigger). Wait for OneDrive to propagate to the other laptop.
8. **Watch it rehydrate on the other laptop.** Start a session in `home` there (or run
   `ccst pdata rehydrate --project home` directly). Confirm the same digest line appears there,
   and step 6's change is now visible on that laptop.
9. **Added beyond what was asked, because this is the other path the whole design exists to get
   right: deliberately force a fork.** Touch `home`'s DB on *both* laptops without letting either
   publish/rehydrate in between (e.g. make a change on laptop A, then on laptop B before laptop A
   has dumped or B has rehydrated). Trigger a dump on each. Confirm that attempting to rehydrate
   on either side is **refused**, not silently resolved - both the SessionStart digest and a
   Telegram message name the fork, point at `ccst pdata resolve --project home`, and ordinary
   `ccst pdata` reads/writes on `home` keep working with the warning banner throughout. Resolve it
   via `ccst pdata resolve` and confirm both laptops agree afterward.

## Relationship to the 2026-08-02 spec

Carried forward: scope (`~/cc`-rooted projects only), snapshots living inside the OneDrive-synced
project folder, "never silently discard a newer write," safe-copy mechanics (SQLite's own
backup/dump API, never a raw file copy of a live DB).

Reversed: automatic triggers now exist (SessionStart, SessionEnd, hourly cron) rather than
exclusively manual push/pull; the whole-machine "no claude process running" gate is replaced by
the narrower per-DB lock check above; a full vector clock replaces plain last-modified-time
staleness comparison, enabling genuine fork *detection* (not just staleness refusal) across any
number of machines; `ccst pdata sync push/pull` naming is replaced by `ccst pdata dump/rehydrate`
to avoid colliding with this spec's different semantics.

Disposition of the old artifacts: the four `docs/superpowers/plans/2026-08-02-multi-laptop-sync-*`
files were never committed (confirmed untracked) and describe the superseded approach - pending
Chris's confirmation, delete them rather than leave two contradictory plans in the repo. The
source spec under `cc-sessions/20260802-.../working/` is left as-is (it's historical record in
the session's own folder, not repo content).

## Open items for the writing-plans pass

- The `project_db_dir()` namespacing/collision fix flagged above is explicitly deferred - decide
  during planning whether it rides along with the separately-tracked `reconcile-session-output`
  root-scoping fix (which already touches the session-index side of the same namespace) or gets
  its own follow-up plan, and either way it needs a migration step for the ~29 `.db` files
  already on disk at the current flat location - not a bare code change.
- Exact `pdata_meta` DDL placement (alongside `_BASE_DDL` in `repository.py`, applied idempotently
  like the existing base tables) and whether per-record-group extension tables need anything
  vector-clock-aware (tentatively no - the vector clock is DB-wide, not per-record-group).
- Confirm current Claude Code hook name for "session end" (referred to here as SessionEnd) against
  the installed hook schema before wiring it.
- The hourly sync job must be registered in `lib/scheduler/bundled_jobs.py` as a **bundled** job
  (not a manually-`ccsched add`ed one) - same treatment as the existing `pdata-verify-all`/
  `pm-session-output-reconcile` entries, so `ccst ccsched-jobs install` (one of
  `install-everything`'s steps) registers it automatically on every fresh install and version
  upgrade, with no separate manual step. Exact job id/cadence to settle during planning.
- `ccst pdata resolve`'s exact per-record diff UX - design during implementation, reusing
  `pm-pdata-conflict-resolution`'s existing display code where possible rather than duplicating it.
- Version bump and CHANGELOG entry for this feature - separate from, and after, the unrelated
  `ccst pdata reconcile-session-output` root-scoping fix already in flight on `f/2.11.1`.
