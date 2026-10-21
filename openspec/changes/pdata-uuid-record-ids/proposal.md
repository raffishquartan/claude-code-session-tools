# Proposal

## Why

`ccst pdata` record ids are SQLite rowids, assigned independently on each machine. Two laptops that
each create a record before they have rehydrated give two unrelated records the same integer id,
and `ccst pdata resolve` cannot reconcile that (`id collision`). One project now reports 44185
records to resolve, `pdata-sync` and `pdata-sync-hourly` refuse to rehydrate, `pdata-verify-all`
failed ten times in a row and was auto-suspended, and `ccst doctor` shows FAIL for two projects.
The failure is structural: it recurs whenever any machine writes before it has rehydrated.

## What Changes

- **BREAKING**: a record's `id` becomes a UUID stored as TEXT (random UUIDv4 for new records)
  instead of an auto-assigned integer. `ccst pdata get/update/delete/restore --id`,
  `resolve --choice ID=local|dump`, and every `id` in table, csv and json output become UUID
  strings.
- **BREAKING**: the published sync dump gets a format version. The UUID-era dump lives in a new
  location, `.pdata-db-dump/v4/`, so a machine still running 3.x never reads or overwrites it. The
  3.x dump files are left untouched.
- New `ccst pdata migrate-uuid` command (`--project` or `--all-projects`; dry run by default,
  `--write` to apply) that converts an existing integer-id database. The old-to-new id mapping is
  deterministic (UUIDv5 over project and old integer id), so every machine that migrates from the
  same converged state ends up with identical ids.
- Migration refuses to run unless the project is converged (no fork, no unpublished local changes,
  local equals the published dump), no `claude` session occupies the project, and the database is
  not locked. It backs up the database first, is idempotent, and records a completion marker.
- Once migrated, the new code refuses to publish or rehydrate across a format mismatch with a clear
  message, and every pdata verb other than `migrate-uuid` refuses on an unmigrated database.
- Fork detection and `resolve` drop the integer-collision classification: the same id on both
  sides can now only be the same logical record. Genuine edit conflicts, delete-versus-update and
  group-mismatch remain.
- Records that are meant to be unique get deterministic ids so two machines creating "the same"
  record produce one record: the session-output watermark, and each registered session-output file.
- Rows ordered by id are ordered by `created_at` then `id`.
- `ccst doctor` gains `migration-to-4.0.0:pdata-uuid` (one result per project) and the
  `pending-migration` hook reports unmigrated projects.
- Skills and docs that describe integer ids and the id-collision case are rewritten; a migration
  runbook for the multi-machine sequence is added.
- Version 4.0.0 (major), CHANGELOG entry.

## Capabilities

### New Capabilities

- `pdata/record-ids`: what a record id is, how ids are generated, accepted and ordered, which
  records get deterministic ids, and how fork classification behaves without integer collisions.
- `pdata/uuid-migration`: the `migrate-uuid` command, its preconditions, backup, determinism,
  idempotency, doctor check and pending-migration reporting, and the multi-machine sequence.
- `pdata/dump-format-version`: the dump format marker, the v4 dump location, and the refusals when
  database and dump formats differ (rehydrate, adopt-from-dump, publish, sync-check).

### Modified Capabilities

None. `pdata/init-write-idempotency` and `pdata/init-adopt-from-dump` are written without reference
to the id type; adoption refusal across formats is specified in `pdata/dump-format-version`.

## Impact

- Code: `src/cc_session_tools/lib/pdata/` (`repository.py` schema and id generation, `service.py`,
  `resolve.py`, `dump.py`, `rehydrate.py`, `sync_check.py`, `verify.py`, `session_output.py`,
  `init_service.py`, `formatting.py`, a new migration module), `cli/ccst.py` (argument types,
  help text, new subcommand), `lib/doctor.py`, the `pending-migration` hook, `src/hooks/pdata_sync.py`
  (format-aware messaging only).
- Data: per-project pdata databases (`records.id`, `ext_*.record_id`,
  `pdata_verify_watermark.record_id`) and the published dumps for every project, on every machine.
- Skills and docs: `pm-pdata-resolve-conflicts`, `pm-pdata-do-init`, `pm-pdata-do-update-project-docs`,
  `pm-pdata-do-update-consuming-skills`, `pm-pdata-do-manual-migration`, the pdata sync design doc.
- Tests: `tests/pdata/*`, `tests/test_ccst_pdata_*_cli.py`, `tests/test_ccst_doctor.py`; the
  id-collision cases in `tests/pdata/test_resolve.py` are replaced.
- Out of scope: repairing today's live forks (done by hand with the 3.x tooling before migrating).
