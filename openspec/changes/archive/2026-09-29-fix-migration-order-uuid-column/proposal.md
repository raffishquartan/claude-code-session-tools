## Why

On a machine that upgrades from a pre-3.0.0 install with both migrations still pending, the
1.0.0 legacy import (`ccst sessions migrate`, also step 1 of `ccst migrate all`, and the command
`ccst doctor`'s `migration-to-1.0.0:sessions` FAIL tells the user to run) crashes with
`sqlite3.OperationalError: table sessions has no column named uuid`. The current code writes
uuid-keyed rows, but the rebuild that adds the `uuid` column (`ccst sessions migrate-uuid
--write`, the 3.0.0 migration) has not run yet, and nothing detects or explains that ordering
dependency. The failed import also leaves partial state behind (tags and mutes already committed,
missing tables/columns already added). Reproduced against seeded pre-3.0.0 `sessions.db` files;
running the two steps in the opposite order succeeds.

## What Changes

- `ccst migrate all` performs the ordering itself: when `sessions.db` is pre-uuid it runs the
  3.0.0 rebuild (with its existing backup and lock guard) before the legacy import, so doctor's
  existing single remediation (`ccst migrate all`) is correct.
- The standalone legacy import (`ccst sessions migrate`) refuses up front, before any write to
  `sessions.db`, when a `sessions` table exists that is not uuid-aware, exiting non-zero and
  naming `ccst sessions migrate-uuid --write` (and its plain-terminal precondition). A missing
  file or missing `sessions` table is not refused - the current schema is created for those.
- `ccst sessions migrate-uuid` validates the `sessions` columns it copies before touching
  anything and names any missing one; tolerates a table without the later-added `updated_at`;
  treats a missing `sessions` table as nothing to migrate; and, when the table is already
  uuid-keyed but the completion marker is missing, records the marker without rebuilding (today it
  crashes with a UNIQUE error after writing a backup for a forked table).
- `ccst doctor`: both `sessions` findings read the same `sessions.db` path, and their reasons say
  the rebuild and the legacy import go together (`ccst migrate all` does both, in order).
- Upgrade-path tests cover both shipped pre-3.0.0 schema shapes (v1.0.0-v2.12.x without
  `updated_at`, v2.13.0-v2.14.x with it), taken verbatim from git history, in a module whose
  docstring and test names say that a failure means the schema changed and the later migration
  steps' tests need updating with it.
- Not adopted: making the 3.0.0 migration refuse until the 1.0.0 import is complete. Only the
  import's activity step and verification need the `uuid` column, but the import as a whole cannot
  complete before the rebuild, so that gate would deadlock the sessions store. See design.md.

## Capabilities

### New Capabilities

### Modified Capabilities
- `sessions-uuid-fork-disambiguation`: adds order-safety requirements for the legacy import,
  `ccst migrate all`, and the schema migration's starting-structure handling; adds an
  upgrade-completes requirement across shipped schema shapes.
- `cli/store-migration-markers`: adds the requirement that `ccst doctor`'s pending-migration
  guidance for `sessions` names a working order.

## Impact

- `src/cc_session_tools/cli/migrate_sessions_db.py`, `src/cc_session_tools/cli/ccst.py`,
  `src/cc_session_tools/lib/sessions_db.py`, `src/cc_session_tools/lib/doctor.py`.
- New tests; CHANGELOG entry and patch version bump (3.9.2): no on-disk format change, crash and
  partial write become a clean refusal or a correct automatic ordering.
