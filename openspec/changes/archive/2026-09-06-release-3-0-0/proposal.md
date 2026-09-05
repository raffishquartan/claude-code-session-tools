## Why

Ctrl-L-Ctrl-L forking produces a second live session (new uuid, new transcript) that keeps the
same tag/basename. `sessions.db`'s `sessions` table has a `(project_dir, basename)` primary key
with no `uuid` column, so every fork's write path (`ensure_session_row`, `touch_last_opened`,
`touch_last_active`) upserts onto the same row via `ON CONFLICT(project_dir, basename)`. There is
structurally no way for two forks to coexist as separate rows today, so `ccl`/`ccs` can never show
more than one entry per tag - a data-model gap, not a display bug. Root cause confirmed by reading
`src/cc_session_tools/lib/sessions_db.py` in full (see `docs/fork-disambiguation-spec.md`).

## What Changes

- **BREAKING**: `sessions` table's primary key changes from `(project_dir, basename)` to
  `(project_dir, basename, uuid)`. This is a full table rebuild (SQLite cannot extend a PK via
  `ALTER TABLE`), not an additive migration - an unmigrated `sessions.db` is unreadable by <3.0.0
  code and a migrated one is unreadable by >=3.0.0 code that hasn't rebuilt it (see Impact).
- `ensure_session_row`, `touch_last_opened`, `touch_last_active` each gain a required
  keyword-only `uuid: str` parameter, threaded from each call site's existing `session_id`.
- `ccs.py` list mode, `ccst sessions list`, `find_matching_sessions`, and `ccr.py`'s resume-target
  resolution each display/resolve one entry per `(basename, uuid)` fork, sourcing each fork's
  size/mtime from its own `<uuid>.jsonl` transcript rather than the shared
  `cc-sessions/<basename>/` directory.
- New two-signal migration-detection mechanism (PK-ordinal check + an explicit
  `sessions-uuid-pk` marker recorded via `db.record_migration()`), distinguishing "never migrated"
  (fresh 3.0.0 install - OK) from "upgraded, not yet migrated" (FAIL) from "migrated" (OK).
- New migration tooling: a dry-run/report step, a pre-migration backup, a concurrency guard
  refusing to run against a live `sessions.db`, and a `custom-title`-transcript-based uuid backfill
  with an explicit reconciliation rule for rows a backfill cannot resolve (no `"unknown"`
  placeholder left unreconciled).
- New `ccst doctor` check that FAILs (not WARNs) when the 3.0.0 schema migration has not run, and
  passes cleanly against both a migrated store and a brand-new fresh-install store.
- New `ccst repair sessions --uuid` verb to list/adopt/delete leftover migration-ambiguous rows.
- Move/delete semantics (`move_session.py`, `delete_session_row`) extended to operate on all of a
  tag's fork rows, not a single row.
- CHANGELOG `[3.0.0]` entry documenting the breaking change, migration path, backup/rollback, and
  the version-skew behavior across a user's two machines (one may lag on <3.0.0 after the other is
  migrated).

## Capabilities

### New Capabilities
- `sessions-uuid-fork-disambiguation`: `sessions.db`'s `sessions` table becomes uuid-aware so that
  Ctrl-L forks of one tag persist as distinct rows; covers the schema/write-path/read-path
  behavior, the migration-detection and migration-execution mechanics, the `ccst doctor` guard,
  and the `ccst repair sessions --uuid` cleanup verb.

### Modified Capabilities
(none - no existing `openspec/specs/` capability documents current `sessions` table behavior, so
there is nothing to write a delta against; this is greenfield spec coverage for a pre-existing,
previously undocumented data model.)

## Impact

- **Code**: `src/cc_session_tools/lib/sessions_db.py` (schema, write-path), `src/cc_session_tools/cli/ccs.py`,
  `src/cc_session_tools/cli/ccr.py`, `src/cc_session_tools/cli/ccst.py` (`sessions list`, new `repair sessions --uuid`),
  `src/cc_session_tools/lib/sessions.py` (`find_matching_sessions`), `src/cc_session_tools/lib/sessions_repair.py`,
  `src/cc_session_tools/lib/session_gc.py`, `src/cc_session_tools/skills/*/scripts/move_session.py` and
  `delete_sessions.py`, `src/cc_session_tools/cli/migrate_sessions_db.py`, `src/cc_session_tools/lib/doctor.py`,
  `src/hooks/session_tag.py` and `src/hooks/after_response.py` (call-site threading).
- **On-disk data**: `~/.local/share/claude/sessions.db`'s `sessions` table is rebuilt in place.
  Pre-migration backup written to the existing `migration-backups/` directory.
- **Cross-machine**: `sessions.db` is machine-local by design; each of the user's machines
  migrates independently and produces independently-backfilled results.
- **Version compatibility**: a migrated `sessions.db` is not readable/writable by <3.0.0 CCST
  (silent failure today - `hooks/session_tag.py` swallows the resulting `sqlite3.Error` to
  stderr only); this must be stated plainly in the CHANGELOG as the reason a downgrade requires
  restoring the pre-migration backup.
- **`install_sync`**: `ensure_synced()` touches `sessions.db`'s `install_sync` table before almost
  every `ccst` command; the new migration verb's exemption status from CCST's own auto-sync must
  be decided in design.md.
