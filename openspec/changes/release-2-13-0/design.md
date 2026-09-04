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
  proven pattern, and is used - not just available - on every table where more than one process
  can plausibly race to update the same row.
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
- Not adding a `version` CAS column to pure key-value upsert tables with a single logical owner
  per key and no read-then-decide workflow above them (`cursors` in ccmsg/ccsched,
  `reconcile_throttle`, `bundled_job_installs`, `doctor_mutes`) - these get `updated_at`/
  `created_at` for the audit trail, but plain `INSERT ... ON CONFLICT DO UPDATE` (already how
  they're all written) is the correct concurrency story for a table with no client-visible read
  step to go stale.
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

### 3. Scope of CAS: only tables with plausible multi-writer races on an existing row

CAS (`version` column + `cas_update`) is added to:
- `ccsched.db`: `jobs` (edited via `ccsched add/edit/remove`, potentially from two terminals),
  `job_state` (written by concurrent hook invocations and manual `ccsched` commands)
- `sessions.db`: `sessions` (written by concurrent SessionStart/Stop hooks across forked
  sessions - see `docs/fork-disambiguation-spec.md` for the related, separately-tracked forking
  work this doesn't depend on or block)
- `ccmsg.db`: `messages` (status transitions - `mark_read`/`claim`/`archive_one` already
  hand-roll a single-purpose version of this; migrate to the shared helper)
- `command-cache.db`: `command_cache` (concurrent `bash-security-review` hook invocations racing
  to record the same command)

CAS is NOT added to (see Non-Goals): `cursors` (both stores), `reconcile_throttle`,
`bundled_job_installs`, `doctor_mutes`, `telemetry_events`, `catchup_events`,
`hook_invocations`, `session_tags`/`install_sync`/`context_overrides` (already
`updated_at`-upserted by key, single logical writer per key, no read-modify-write gap to close).

### 4. Migration markers: exact TODO.md plan, no changes

Implements TODO.md's "Migration markers for ccmsg, ccsched and sessions" verbatim: append
`db.MIGRATIONS_DDL` to each of the three stores' DDL, add a per-store marker-name constant,
record the marker inside the same transaction as the migration's writes (matching
`migrate_telemetry.py`'s documented reasoning), switch `doctor.check_pending_data_store_migration`
to read markers for all four stores, delete `_count_new_store_rows`. Backfill decision (per
TODO.md's open question): a store whose legacy sources are already gone on this machine has
migrated by definition - write the marker on first `connect()` in that case rather than leaving a
machine that never had legacy data permanently `WARN`-ing about an unmigrated store it never had
data for.

### 5. Version-policy classification: minor, not major

Every schema change here is strictly additive (`ADD COLUMN`, new `migrations` table) against
`CREATE TABLE IF NOT EXISTS` - old code reading these stores ignores unknown columns/tables
without erroring, and no existing on-disk row becomes unreadable. Per this repo's version policy
this is a **minor** bump (2.13.0), not major - contrast with the separately-tracked fork
disambiguation work (`docs/fork-disambiguation-spec.md`, task #5), which changes a PRIMARY KEY
and genuinely does need a major bump and doctor FAIL-guard.

## Risks / Trade-offs

- [Risk] `command-cache.db`'s `cache.py` wraps every DB call in `try/except sqlite3.Error: pass`
  ("never raise from cache") - a CAS conflict there must not be silently swallowed as if it were
  a generic DB error, or callers would never learn `cache_record` lost a race.
  → Mitigation: `cache_record`'s CAS check happens inside the existing try/except (a CAS "loss"
  is `cur.rowcount == 0`, not a `sqlite3.Error`), so it surfaces as a normal Python `bool`, not an
  exception - the existing exception boundary is untouched and the risk does not materialize.
  Covered by a test that forces a CAS loss and asserts `cache_record` handles it (retries with a
  fresh read, per this table's existing "last write wins on re-fire" semantics) rather than
  raising.
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
