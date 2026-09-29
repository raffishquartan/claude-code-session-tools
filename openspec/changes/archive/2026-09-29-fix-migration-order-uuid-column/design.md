## Context

Two one-shot migrations touch `sessions.db`. The 1.0.0 legacy import (`run_migration`,
`ccst sessions migrate`) copies flat tag/activity/mute files in; since 3.0.0 it writes rows via
`sessions_db.ensure_session_row(..., uuid=...)`, whose SQL needs the 3-column key. The 3.0.0
rebuild (`migrate_uuid`) is the only thing that gives a pre-3.0.0 `sessions.db` that column.
Nothing enforces or explains the order. Reproduced: a seeded 2-column `sessions.db` plus a
legacy tag file and session directory make `ccst sessions migrate` raise
`table sessions has no column named uuid` after the tags were already committed. The same seed
succeeds with `migrate-uuid --write` first. `ccst doctor` lists the 1.0.0 FAIL (naming
`ccst migrate all`) alongside the 3.0.0 FAIL with no order.

Also observed: `migrate_uuid` reads legacy rows over a plain connection selecting `updated_at`,
a column added later by `_BACKFILL_COLUMNS`; a `sessions` table that predates it fails the read.

## Goals / Non-Goals

**Goals:** make the wrong order impossible to hit silently (refuse before any write, with the
fix named); make doctor's guidance follow a working order; pin the 1.0.0-era structure by name.

**Non-Goals:** auto-running the rebuild from the standalone import; changing either migration's
data semantics; touching other stores; making readers (`ccs`/`ccl`/`ccr`, `ccst sessions list`,
`ccst repair sessions`, move/delete session), which still raise a raw `no such column: uuid` on a
pre-uuid database, print a friendly error - a follow-up (one shared uuid-schema requirement error
naming `migrate-uuid --write`). Hooks already swallow this error.

## Decisions

1. **Rebuild first, then import; `migrate all` does the ordering.** Rejected: making the 3.0.0
   migration require the 1.0.0 import. Only the import's activity step and its verification need
   the `uuid` column (tags and mutes write fine to a pre-uuid file), but the import as a whole
   cannot complete before the rebuild, so that gate deadlocks the sessions store. Legacy rows
   imported after the rebuild get their uuid from the transcript exactly as the rebuild's own
   backfill does, so the order loses nothing. `ccst migrate all` is already the explicit
   migration command: it requires a plain terminal, and `migrate_uuid --write` brings its own
   lock guard and backup. So it runs the rebuild itself, which keeps doctor's single existing
   remediation correct. The standalone `ccst sessions migrate` does not auto-run the rebuild: a
   command named for the legacy import silently rebuilding a table would be surprising, and it
   refuses with the exact command instead. Rejected: teaching the import to write the 2-column
   shape (keeps a write path alive for a retired schema).
2. **Import guard predicate: refuse only when a `sessions` table exists and is not uuid-keyed.**
   `sessions_schema_is_uuid_keyed` also returns False for a missing table, so the guard must
   check table existence first; a missing file/table passes because `sessions_db.connect()`
   creates the current schema. The guard runs before any `sessions_db.connect()`,
   `doctor_mutes.add_mute()` or backup, on a read-only connection, in dry-run too, and converts a
   `sqlite3.DatabaseError` into a clean non-zero exit.
3. **`migrate-uuid` starting-structure handling** as a `sessions_db` function returning the
   missing `sessions` column names against the six columns the rebuild copies; `updated_at` is
   optional and the legacy read selects it only when present. Outcomes: missing table -> create
   current schema via `connect()`, record marker, exit 0; uuid-keyed without marker -> record
   marker only; otherwise validate then rebuild. The contract is `sessions` only: the other
   tables are created on demand by `connect()`.
4. **`migrate all` sequencing** is in `_cmd_migrate_all`'s sessions step: pre-uuid ->
   `migrate_uuid(dry_run=...)`, then (write mode, rebuild rc 0) the import. In dry-run the import
   is skipped with a note, since its guard would refuse. A failing/refused rebuild sets the step's
   rc non-zero and skips the import; the other steps continue as today. The closing text names
   failed steps rather than always saying "re-run without --dry-run".
5. **Doctor wiring:** `check_pending_data_store_migration` and `check_sessions_uuid_migration`
   both use the one `sessions_db_path` passed to `run_all_checks` (today they derive it from
   `CCST_DATA_HOME` and `CCST_SESSIONS_DIR` respectively and can disagree). The 1.0.0 check gains
   an optional `sessions_db_path` argument; the 3.0.0 check gains a `legacy_sessions_pending`
   flag computed by the caller. Both failing branches of the 3.0.0 check get the follow-on note.
6. **Tests** live in `tests/test_sessions_db_migration_chain.py`. The two baselines are literal
   DDL copied from `git show v1.0.0:` and `git show v2.14.1:` `sessions_db.py` (independent of
   the current `sessions_db.DDL` so a later schema change cannot silently alter them), the
   upgrade-path test is parametrized over both, and change detection uses snapshots of
   `sqlite_master`, `PRAGMA table_info(sessions)` and row counts. `migrate all` tests monkeypatch
   the ccmsg/ccsched/telemetry step functions to recording stubs: their default paths are
   import-time `Path.home()` values and their real runs delete files. The existing
   `_seed_old_schema_db` docstring is corrected (it is the v2.13+ shape only).

## Risks / Trade-offs

- [Standalone `sessions migrate` still needs the manual order] -> its refusal names the command;
  the common path (doctor -> `migrate all`) is automatic.
- [Pinned literal DDL drifts from reality] -> it represents a historical shipped schema, not the
  current one, so it should not change; the test docstring says so.
- [Structure check too strict] -> limited to columns the rebuild copies, with `updated_at`
  optional.

## Migration Plan

No on-disk change. Ship as patch 3.9.2. Rollback is reverting the release.
