## 1. `cccs_hooks` → `hooks` rename

- [x] 1.1 `git mv src/cccs_hooks src/hooks`; replace every `cccs_hooks` reference with `hooks` in
  live source/docs (`src/`, `tests/`, `scripts/`, `pyproject.toml`, `README.md`, `TODO.md`) -
  leave historical records (`CHANGELOG.md`, `docs/superpowers/**`, `openspec/changes/archive/**`)
  untouched. Verify: `grep -rl cccs_hooks src/ tests/ scripts/ pyproject.toml README.md TODO.md`
  returns nothing.
- [x] 1.2 Update `pyproject.toml`'s `cccs-stats` entry point target to `hooks.stats:main` (keep
  the console-script name `cccs-stats` itself unchanged - see design note); fix the package
  description string. Verify: `grep cccs-stats pyproject.toml` shows the new target.
- [x] 1.3 Remove the now-resolved "Tidy up `catchup.py` and siblings' module path" TODO.md
  section (this task closes it). Verify: `grep -n "Tidy up" TODO.md` returns nothing.
- [x] 1.4 Full test suite passes with the renamed package. Verify: `uv run pytest -q` green.

## 2. Fresh-install packaging smoke test

- [x] 2.1 Read `doctor.py`'s `_db_connect` in full (flagged as unread by the research pass) and
  confirm whether it duplicates `lib/db.py:connect()` or is a separate path - note the finding in
  a code comment near `_db_connect` if it's a genuine duplicate, but do not refactor it (out of
  scope per design.md). Finding: `_db_connect` (doctor.py:23) is just
  `from cc_session_tools.lib.db import connect as _db_connect` - an aliased import, not a
  duplicate. No comment needed; the actual duplication the research flagged is in
  `_telemetry_import_recorded`'s query logic (task 5.4), not the connection helper.
- [x] 2.2 Manual smoke test: build a wheel (`uv build`), install it into a throwaway clean venv
  (no editable install, no source checkout on `PYTHONPATH`), run
  `ccst install-everything --apply --source <nonexistent>` omitted (no `--source` flag) and
  confirm it locates bundled `skills/`/`config/`/`prompts/` and completes without a
  "cannot locate bundled ..." error. Verify: command exits 0; document the exact commands run in
  this task's checkbox description or a follow-up note. Done: `uv build`, `uv tool install
  <wheel>` into an isolated `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`, against a fake empty `$HOME`, ran
  `ccst doctor` (correctly enumerated all 26 bundled skills and 14 bundled hooks with no
  discovery error) then `ccst install-everything --apply` (exit 0, "All install steps complete",
  19 skills symlinked, 9 scheduled jobs registered, CLAUDE.md block written) - no manual
  `--source` override needed at any point.
- [x] 2.3 Add an automated regression test exercising `_discover_source_dir`/`_discover_bundle`
  (and the prompts-dir equivalent) against a simulated installed-package layout (e.g. a temp dir
  laid out like `site-packages/cc_session_tools/{skills,config,prompts}` with
  `cli/ccst.py`-relative discovery pointed at it), not just the source-tree layout the existing
  tests already cover. Verify: new test fails if `_discover_source_dir` is reverted to a
  filesystem-walk-up implementation, passes against current code. Done:
  `test_discovery_succeeds_against_a_simulated_installed_package_layout` in
  `tests/test_ccst_bundle_discovery.py` - confirmed the existing tests only ever exercised this
  repo's editable install (`import cc_session_tools; print(cc_session_tools.__file__)` resolves
  to `src/cc_session_tools/`), never a real flat site-packages layout.

## 3. Shared CAS primitive

- [x] 3.1 Write a failing test for `lib/db.py`'s new `cas_update()` (or equivalent name):
  succeeds and advances version when called with the current version; returns `False` (zero rows
  changed) when called with a stale version; test with a plain temp SQLite table, not any real
  store's schema. Verify: test fails (function doesn't exist yet).
- [x] 3.2 Implement `cas_update()` in `lib/db.py` per design.md Decision 1 (caller-supplied SET
  clause + params, `WHERE id=? AND version=?`, `rowcount==1` return). Verify: 3.1's test passes.
- [x] 3.3 Confirm `lib/pdata/repository.py` is left untouched (design.md Non-Goals - not
  refactored to call the new helper in this change). Verify: `git diff` shows no changes under
  `lib/pdata/`. Confirmed: `git diff --stat -- src/cc_session_tools/lib/pdata/` is empty.
- [x] 3.4 Add `lib/db.py:add_missing_columns()`, generalizing `scheduler/store.py`'s
  per-store `_migrate_jobs_table` idiom (PRAGMA table_info + ALTER TABLE ADD COLUMN for each
  absent column) so section 4's ~15 new columns across 3 stores don't each hand-roll the same
  check. Verify: `test_add_missing_columns_adds_only_absent_columns`,
  `test_add_missing_columns_is_idempotent_and_preserves_data` in `tests/test_db.py`.

## 4. `created_at`/`updated_at` + CAS wiring per store

**Scope revised during 4.1** (see design.md Decision 3): `version`/`cas_update()` wiring is
scoped to `ccsched.db`'s `jobs` table only - the one write path that is a genuine
read-then-decide-then-write workflow. Every other table below still gets `created_at`/
`updated_at`; none of the others get an unused `version` column.

- [x] 4.1 `ccmsg.db`: add `updated_at`+`version` to `messages` (via `ALTER TABLE` idiom matching
  `_migrate_jobs_table` - `version` added for audit/future-readiness per design.md Decision 3,
  not wired to `cas_update()`), add `created_at`+`updated_at` to `cursors`. Bump `updated_at` (and
  increment `version`) on every write in `mark_read`/`claim`/`archive_one`/`archive_aged`/
  `mark_receipts_shown`/`refresh_display_tags` - keeping each function's own existing conditional
  guard (e.g. `WHERE status='sent'`) exactly as-is, not replaced by `cas_update()` (see design.md
  Decision 3 for why). `save_cursor` sets `cursors.created_at`/`updated_at`. Verify: one test per
  write path in `common-store-cas-research.md` Section 1's table asserting `updated_at`/`version`
  change after the call; existing `test_mark_read_is_first_writer_wins` and
  `test_second_claim_raises_already_claimed` continue to pass unchanged (guards untouched).
- [x] 4.2 `ccsched.db`: add `created_at`+`updated_at`+`version` to `jobs` (`created_at` is
  genuinely new - `jobs` has no existing first-insert column at all); add `updated_at` to
  `job_state` (no new `created_at` - `registered_at` is already immutable in every production
  write path, per design.md Decision 3a); add `created_at`+`updated_at` to `cursors`; add a real
  `created_at` (new column, written once, excluded from each table's `DO UPDATE`/`OR REPLACE`
  clause so it survives later upserts) to `reconcile_throttle` and `bundled_job_installs` -
  `last_reconciled_at`/`installed_at` are themselves mutable (last-seen, not first-seen), switch
  `mark_bundled_installed` from `INSERT OR REPLACE` to `INSERT ... ON CONFLICT DO UPDATE` to make
  preserving `created_at` possible. Migrate every write path in the research doc's Section 2
  table to bump `updated_at`. Migrate `jobs`' `replace_job` to `cas_update()`, threading the
  version `_cmd_edit` already has from its `load_registry()` read (add `version: int = 1` to
  `JobSpec`, defaulted so every other construction site is unaffected); raise a new
  `JobVersionConflictError(RegistryError)` distinct from "unknown job id" on a CAS loss.
  `set_enabled`/`rename_job` keep their existing unconditional writes (not a read-then-decide
  workflow - re-read `registry.py`'s job-mutating functions and their CLI callers in full first).
  `job_state` writers get `updated_at` only, no CAS (design.md Decision 3). Verify: one test per
  write path asserting `updated_at` changes; a CAS-loss test for `replace_job` (two concurrent
  edits to the same job - the second, using a stale version, raises
  `JobVersionConflictError` rather than silently overwriting); a test that `bundled_job_installs`'s
  `created_at` survives a reinstall while `installed_at` updates; full existing `ccsched` CLI test
  suite green (proves no legitimate single-writer call site regresses).
- [x] 4.3 `sessions.db`: add `updated_at` to `sessions` (no `version`/CAS - design.md Decision 3;
  `discovered_at` already serves as an immutable `created_at`, no new column needed there); add a
  real `created_at` to `doctor_mutes` (new column, preserved across `add_mute`'s existing
  `ON CONFLICT DO UPDATE` - `muted_at` is mutable, last-muted not first-muted, per design.md
  Decision 3a); add `created_at` to `session_tags`, `install_sync`, `context_overrides` (already
  have `updated_at` but no immutable first-write column - a genuine gap, not redundant). Migrate
  `touch_last_opened`/`touch_last_active` to bump `updated_at`. Verify: write-path tests per the
  research doc's Section 3 table; a test that `doctor_mutes.created_at` survives a re-mute.
- [x] 4.4 `telemetry.db`: confirmed no changes needed (design.md Non-Goals - pure append-only
  event tables, existing `ts` column already serves as `created_at`). Verify: no code change;
  note this explicitly in the PR description rather than silently skipping.
- [x] 4.5 `command-cache.db`: add `created_at` to `command_cache` (`validated_at` already
  functions as `updated_at`, kept as-is; no `version`/CAS - `cache_record`'s
  `ON CONFLICT DO UPDATE SET fire_count=fire_count+1` is already one atomic statement with no
  read-then-write gap, per design.md Decision 3). Verify: a test asserting `created_at` is set on
  first insert and unchanged by a subsequent `cache_record` re-fire for the same hash.
- [x] 4.6 Full test suite green after all 5 stores' changes. Verify: `uv run pytest -q`.

## 5. Migration markers (ccmsg, ccsched, sessions) + doctor rewiring

- [x] 5.1 Append `db.MIGRATIONS_DDL` to each of the three stores' `_DDL`/`DDL` constants; define
  a marker-name constant per store (matching `telemetry_store.LEGACY_JSONL_MIGRATION`'s naming
  style). Verify: a fresh DB for each store has a `migrations` table.
- [x] 5.2 Locate each store's one-shot legacy-data migration entry point (mirroring
  `cli/migrate_telemetry.py`) and add `db.migration_applied()` as the refuse-if-already-run guard
  at the top (matching `migrate_telemetry.py`'s `_already_imported`/refusal shape). Record
  `db.record_migration()` per design.md Decision 4's per-script refinement (revised further during
  implementation - `_write_db`'s commit happens unconditionally before `_verify()` runs even for
  `migrate_ccsched.py`, so all three scripts record the marker as the last write, right after
  verification passes and before any backup/cleanup step - never on unverified data, safe to
  retry from any earlier crash point since all three scripts' writes are already idempotent
  (`INSERT OR IGNORE`/`ON CONFLICT DO NOTHING`)). Refuse-if-already-run is scoped to when the
  legacy source actually has content (`migrate_ccsched.py`/`migrate_ccmsg.py`) - **not** added to
  `migrate_sessions_db.py`, which by explicit design never deletes its old flat-file sources (only
  prints the `rm` command for the user to run by hand), so those sources are present on every
  re-run regardless of migration state; a refusal there would break the intentionally-supported
  repeated-run workflow `test_run_twice_is_idempotent` already exercises - it only gains the
  marker, for `ccst doctor` to read. Verify: one test
  per store - running the migration twice only writes data once (or refuses outright); the marker
  is present after a successful run; a test that a verification failure leaves the marker unset.
- [ ] 5.3 Backfill: on first non-readonly `connect()` to a store whose legacy sources are already
  absent on this machine, record the marker (design.md Decision 4). Verify: a test connecting to
  a fresh store with no legacy sources present asserts the marker ends up recorded, not left
  permanently absent.
- [ ] 5.4 Rewrite `doctor.py`'s `_telemetry_import_recorded` to call `telemetry_store.connect()` +
  `db.migration_applied()` instead of its own `_db_connect` + duplicated query. Verify: existing
  telemetry-migration doctor tests still pass.
- [ ] 5.5 Switch `check_pending_data_store_migration`'s ccmsg/ccsched/sessions branch from
  `_count_new_store_rows` to the new marker check (same shape as the telemetry branch); delete
  `_count_new_store_rows` entirely once nothing calls it. Verify:
  `grep -n _count_new_store_rows src/cc_session_tools/lib/doctor.py` returns nothing; a doctor
  test asserts a store with rows-but-no-marker is reported pending (the exact bug this task
  fixes), and a store with a marker is reported complete regardless of row count.
- [ ] 5.6 Remove the now-resolved "Migration markers for ccmsg, ccsched and sessions" TODO.md
  section. Verify: `grep -n "Migration markers" TODO.md` returns nothing.

## 6. Release

- [ ] 6.1 Full test suite green end-to-end. Verify: `uv run pytest -q`.
- [ ] 6.2 Update `CHANGELOG.md` for `[2.13.0]` (Changed: `cccs_hooks` → `hooks` rename; Added:
  common-store audit columns + CAS support, migration-completion markers for ccmsg/ccsched/
  sessions; Fixed: `ccst doctor`'s row-count-based migration inference). Commit.
- [ ] 6.3 Bump `pyproject.toml` version to `2.13.0`; run `CCST_NO_AUTO_SYNC=1 uv lock`; commit
  both together (follow-on commit per this repo's convention).
- [ ] 6.4 Sync specs (`cli/store-audit-columns`, `cli/store-migration-markers`,
  `cli/packaging-fresh-install`) and archive this change.
- [ ] 6.5 Push `f/2.13.0`, open a PR (base `main`) - recommend title/body, confirm with the user
  before running `gh pr create`.
