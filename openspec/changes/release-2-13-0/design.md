## Context

See proposal.md - Why. This is a cross-cutting change touching 5 SQLite stores
(`ccmsg.db`, `ccsched.db`, `sessions.db`, `telemetry.db`, `command-cache.db`) plus `ccst doctor`,
so a design pass is warranted before coding.

Research (`cc-sessions/20260904-update-ccst/working/common-store-cas-research.md`, this repo)
confirms:
- All 5 stores already route through the single shared `lib/db.py:connect()` helper - no
  connection/WAL drift to fix.
- No generic CAS primitive exists in `lib/db.py`, but `lib/pdata/repository.py` +
  `lib/pdata/service.py` already implement one in full for the `records` table: a
  `version INTEGER NOT NULL DEFAULT 1` column, a `WHERE id=? AND version=?` conditional UPDATE,
  `cur.rowcount == 1` as the win/lose signal, and a `VersionConflictError(current, attempted)`
  the CLI surfaces as a diff. This is the pattern to generalize, not a from-scratch design.
- `db.MIGRATIONS_DDL`'s two helpers (`migration_applied`, `record_migration`) are already
  fully generic (take a connection + a free-form `name` string) - only the per-store DDL
  inclusion, marker-name constant, and migration-script wiring are store-specific.
- `created_at`/`updated_at` coverage today is inconsistent both across and within stores (full
  per-table, per-write-path inventory in the research doc).

## Goals / Non-Goals

**Goals:**
- Every table across the 5 stores that is ever updated in place gets an `updated_at` column,
  bumped on every write that touches an existing row.
- Every table gets a `created_at` column reflecting first-insert time (added even to tables that
  already have a domain-specific first-seen column, for a uniform audit story).
- A generic, reusable CAS helper lives in `lib/db.py`, extracted from `lib/pdata/repository.py`'s
  proven pattern, and is used - not just available - on every table with a genuine
  read-then-write staleness gap (see Decision 3 - narrower than "more than one process can touch
  it", since several tables are already race-safe by construction).
- `ccmsg`, `ccsched`, and `sessions` each gain an explicit, durable migration-completion marker
  (via `db.MIGRATIONS_DDL`, matching telemetry), and `ccst doctor`'s
  `check_pending_data_store_migration` reads that marker for all four stores. The
  `_count_new_store_rows` row-count-inference branch is deleted, not just bypassed.
- `doctor.py`'s `_telemetry_import_recorded` is rewritten to call `telemetry_store.connect()` +
  `db.migration_applied()` instead of re-implementing the same query against its own
  `_db_connect` helper - one implementation of "is this marker set", not two.

**Non-Goals:**
- Not adding CAS-guarded updates to pure append-only event-log tables (`telemetry_events`,
  `catchup_events`, `hook_invocations`) - these tables are never updated in place (only
  inserted/pruned), so "compare and swap" does not apply; they still get `created_at` via their
  existing `ts`/timestamp column being treated as such (no new column needed - already present).
- Not adding a `version` CAS column to any table without a genuine read-then-decide-then-write
  workflow above it - see Decision 3 for the full per-table accounting. This turned out to be
  everything except `ccsched.db`'s `jobs`: single-field flips reacting to one event
  (`job_state`, `sessions`), domain-guarded conditional updates that already prevent the lost
  update they'd otherwise risk (`messages`), a single atomic increment statement
  (`command_cache`), and plain key-value upserts with no client-visible read step to go stale
  (`cursors` in ccmsg/ccsched, `reconcile_throttle`, `bundled_job_installs`, `doctor_mutes`) all
  get `updated_at`/`created_at` for the audit trail, but not `version`/CAS.
- Not touching `lib/pdata/`'s own CAS implementation - it stays the reference/source for the
  extraction, but this change does not refactor pdata to call the new shared helper (separate
  follow-up, out of scope here to avoid touching a stable, already-shipped subsystem
  incidentally).
- Not a schema-breaking change: every new column is added via `ALTER TABLE ... ADD COLUMN`
  against `CREATE TABLE IF NOT EXISTS` (the established idiom already used by
  `scheduler/store.py:_migrate_jobs_table` for `success_exit_codes`), so old rows keep working
  and no `ccst doctor` FAIL-guard or major version bump is needed (see version-policy note below).

## Decisions

### 1. CAS helper: extract from pdata, generalize on (table, id_column, version_column)

New functions in `lib/db.py`:
```python
def cas_update(
    conn: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    id_value: object,
    version_column: str,
    expected_version: int,
    set_clause: str,
    params: tuple[object, ...],
) -> bool:
    """Conditionally UPDATE one row, guarded by its version. Returns True iff exactly one row
    changed. Caller supplies the SET clause and its params (e.g. "content=?, updated_at=?,
    " + version_column + "=" + version_column + "+1") - this stays a thin, generic primitive,
    not an ORM."""
```
`set_clause` deliberately stays caller-supplied text (not a dict of column->value) so each call
site controls its own `<version_column> = <version_column> + 1` increment and any
`COALESCE(?, existing)`-style partial-update semantics, exactly like pdata's
`update_base_record` does today. Rationale: a fully generic dict-based builder would need to
special-case increment-vs-overwrite per column, adding complexity for no caller this change has.

**Alternative considered:** leave CAS logic duplicated per store (ccmsg's `mark_read` pattern,
repeated 5 times). Rejected per this repo's coding standard ("hoist shared logic to one source of
truth... never copy 'just for now'") - the pattern is proven and already has a conflict-resolution
UX precedent (`pm-pdata-conflict-resolution` skill) worth reusing conceptually for future
non-pdata conflict surfacing.

### 2. Version column vs. updated_at-based CAS

Tables gaining CAS support (see scope below) get a `version INTEGER NOT NULL DEFAULT 1` column
(pdata's shape), not an `updated_at`-based compare - a version counter is race-proof at
sub-timestamp-resolution, while comparing `updated_at` strings/floats is not (two writers in the
same tick would both "match"). `updated_at` stays a separate, purely informational audit column
alongside `version` on these tables.

### 3. Scope of CAS: only tables with a genuine read-then-write staleness gap

**Revised during implementation** (task 4.1): CAS solves one specific problem - a caller reads a
row, decides a new value based on what it read, and writes back; the write must fail if the row
changed since that read. Working through each store's actual write paths (not just their table
names) shows that gap is much narrower than "any table more than one process might touch":

- **`ccsched.db`: `jobs`** - the one clear case. `ccsched edit <job-id>` reads the job's current
  definition, a user edits it, and `replace_job` writes the whole row back; two terminals editing
  the same job concurrently is a real, silent lost-update risk with no read-then-write gap
  today. Gets `version` + `cas_update()`.
- **`ccsched.db`: `job_state`** - re-examined, NOT added. Its writers (`set_in_flight`,
  `record_success`, `record_failure`, ...) each flip specific fields reacting to one job
  execution event; they don't read the row, decide from what they read, and write back a
  computed whole-row value. The same job normally only has one execution in flight at a time by
  design (that's what `in_flight_pid` itself guards). `updated_at` still added for the audit
  trail; no `version`/CAS.
- **`ccmsg.db`: `messages`** - re-examined, NOT wired to `cas_update()`. `mark_read`/`claim`
  already guard against the lost-update race with `WHERE ... AND status='sent'` /
  `status NOT IN (...)` - a direct conditional command on the field that matters, not a
  read-then-blind-overwrite. That guard is not weaker than version-based CAS for this shape of
  write; replacing it with `cas_update()`'s generic `WHERE id=? AND version=?` would actually
  *drop* the status precondition (a caller could win a version match while overwriting a status
  another write legitimately set). `updated_at` + `version` are still added (for audit, and so a
  future genuine read-modify-write caller has something to key off), but no existing write path
  changes its guard.
- **`sessions.db`: `sessions`** - re-examined, NOT added. `touch_last_opened`/`touch_last_active`
  each unconditionally set one timestamp field to "now" - not a read-then-decide write. The
  actual multi-writer risk here is the *forking* problem in `docs/fork-disambiguation-spec.md`
  (task #5), which is a different bug (missing PRIMARY KEY dimension) that a `version` column on
  the current 2-column key would not fix. `updated_at` still added for the audit trail.
- **`command-cache.db`: `command_cache`** - re-examined, NOT added. `cache_record`'s
  `ON CONFLICT DO UPDATE SET fire_count=fire_count+1` is a single atomic statement - SQLite
  serializes it under WAL with no read-then-write gap for `cas_update()` to close.
  `created_at` still added for symmetry.

Net effect: `version` + `cas_update()` wiring is scoped to `ccsched.db`'s `jobs` table only.
Every other table in scope for this change gets `created_at`/`updated_at` as originally planned;
none of them get an unused `version` column added on the strength of a race that, on inspection,
their existing write shape doesn't actually have - adding one would be exactly the kind of
"defensive code for a state that cannot occur" this repo's coding standards rule out.

### 3a. `created_at`: reuse an existing immutable column where one already exists

The store-audit-columns spec's own requirement text already allows this ("or an existing
domain-specific column that already unambiguously records first-insert time"). Checked per
table, by reading whether the column is ever touched by an UPDATE/upsert after first insert:

- **Already immutable, no new column**: `job_state.registered_at` (only written by
  `ensure_registered_db`'s `INSERT OR IGNORE` - no production write path ever updates it),
  `sessions.discovered_at` (`ensure_session_row`'s `INSERT ... ON CONFLICT DO NOTHING`).
- **Mutable despite the name, needs a real `created_at`**: `bundled_job_installs.installed_at`
  (`mark_bundled_installed` currently does `INSERT OR REPLACE`, resetting it on every
  reinstall), `reconcile_throttle.last_reconciled_at` (upserted every reconcile by design - it's
  a last-seen timestamp, not first-seen), `doctor_mutes.muted_at` (upserted on every re-mute).
  Each gets a genuine `created_at`, written once on first insert and excluded from that table's
  `DO UPDATE SET` clause so it survives later upserts (`mark_bundled_installed` also switches
  from `INSERT OR REPLACE` to `INSERT ... ON CONFLICT DO UPDATE` for this reason - REPLACE has
  no way to preserve a column across the conflict).

### 4. Migration markers: exact TODO.md plan, refined per script during 5.2

Implements TODO.md's "Migration markers for ccmsg, ccsched and sessions": append
`db.MIGRATIONS_DDL` to each of the three stores' DDL, add a per-store marker-name constant,
switch `doctor.check_pending_data_store_migration` to read markers for all four stores, delete
`_count_new_store_rows`. Backfill decision (per TODO.md's open question): a store whose legacy
sources are already gone on this machine has migrated by definition - write the marker on first
`connect()` in that case rather than leaving a machine that never had legacy data permanently
`WARN`-ing about an unmigrated store it never had data for.

**Where the marker is recorded, refined during implementation:** `migrate_telemetry.py`'s "same
transaction as the writes" guarantee assumes one connection, one transaction, one commit for the
whole migration - true there, but `migrate_ccmsg.py` and `migrate_sessions_db.py` are each
structured as several independent commit points (per-message/per-tag/per-sentinel writes, or
several `_migrate_*` helpers each with their own connection), with verification running *after*
those commits, not inside them. Restructuring three already-working, already-tested migration
scripts' transaction models to force single-transaction atomicity is out of proportion to what
this task needs, and unnecessary: all three scripts already write via `INSERT OR IGNORE` /
`ON CONFLICT DO NOTHING`, so a re-run after a crash mid-write safely converges instead of
duplicating (unlike telemetry, which appends and cannot tolerate a double-import). Given that,
each of the three scripts - `migrate_ccsched.py` included, despite its `_write_db` being one
connection/one commit like telemetry's - records its marker as the last write, right after
verification passes and before any backup/cleanup step, in its own small transaction: unlike
telemetry, `_write_db` commits unconditionally and `_verify()` checks the result afterward
(existing design, unchanged here), so a marker written inside `_write_db`'s own commit would mark
a verification failure as migrated. Recording it after verification everywhere keeps the actual
guarantee that matters uniform across all three scripts: no store is ever marked migrated on
unverified data, and every script is safe to retry from any earlier crash point because its
writes are idempotent.

### 5. Version-policy classification: minor, not major

Every schema change here is strictly additive (`ADD COLUMN`, new `migrations` table) against
`CREATE TABLE IF NOT EXISTS` - old code reading these stores ignores unknown columns/tables
without erroring, and no existing on-disk row becomes unreadable. Per this repo's version policy
this is a **minor** bump (2.13.0), not major - contrast with the separately-tracked fork
disambiguation work (`docs/fork-disambiguation-spec.md`, task #5), which changes a PRIMARY KEY
and genuinely does need a major bump and doctor FAIL-guard.

## Risks / Trade-offs

- [Risk] `ccsched.db`'s `jobs` is the one table gaining real CAS enforcement
  (`replace_job`/`set_enabled`/`rename_job`) - an existing caller that reads a job then writes it
  back without threading the version it read would now fail unexpectedly.
  → Mitigation: `registry.py`'s job-mutating functions are re-read in full before wiring CAS in
  (task 4.2), and every caller updated in the same commit; a CAS-loss test proves the *rejection*
  path works, and the full existing `ccsched` CLI test suite proves no legitimate single-writer
  call site regresses.
- [Risk] Adding `updated_at` bumps to ~30 write paths across 3 modules is mechanical but
  high-surface-area; a missed call site silently leaves a table with an occasionally-stale
  `updated_at`.
  → Mitigation: the research doc's write-path inventory (Sections 1-3) is the checklist for
  tasks.md; each store's task is "every write path in the inventory", not "some write paths",
  and each gets its own test asserting `updated_at` changed after the call.
- [Risk] `doctor.py`'s `_db_connect` (used by the migration-marker checks) was not fully read in
  the research pass - unclear if it duplicates `lib/db.py:connect()` or is a genuinely separate
  path.
  → Mitigation: read it fully as the first implementation task, before writing any doctor.py
  changes; if it is a duplicate connection helper, note but do not fix it here (out of scope -
  this change only needs it to keep working for readonly marker checks).

## Migration Plan

Purely additive - no explicit migration step or backfill script needed beyond what's already
described in Decision 4 (marker backfill on first connect when legacy sources are absent).
`ALTER TABLE ... ADD COLUMN` runs automatically on every non-readonly `connect()` against an
existing DB file, same idiom as `scheduler/store.py:_migrate_jobs_table`. No rollback concern:
old code ignores the new columns/table; this branch is the only code that writes to them.

## Open Questions

None - all decisions above are resolved for this change. (Fork disambiguation's own open
questions live in `docs/fork-disambiguation-spec.md`, tracked separately as task #5.)
