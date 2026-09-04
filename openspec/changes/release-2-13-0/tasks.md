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

- [ ] 4.1 `ccmsg.db`: add `updated_at` to `messages` (via `ALTER TABLE` idiom matching
  `_migrate_jobs_table`), add `created_at`+`updated_at` to `cursors`, add a `version` column to
  `messages` and migrate `mark_read`/`claim`/`archive_one`/`archive_aged`/
  `mark_receipts_shown`/`refresh_display_tags` to bump `updated_at` on every write; migrate
  `mark_read`/`claim` to use `cas_update()` in place of their hand-rolled conditional UPDATEs.
  `save_cursor` sets `cursors.updated_at`. Verify: one test per write path in
  `common-store-cas-research.md` Section 1's table asserting `updated_at` changes after the call;
  a CAS-loss test for `mark_read`/`claim` (concurrent status flip) asserting the second caller's
  write is rejected, not silently applied.
- [ ] 4.2 `ccsched.db`: add `created_at`+`updated_at`+`version` to `jobs`; add `updated_at` to
  `job_state`; add `created_at`+`updated_at` to `cursors`; add `created_at` to
  `reconcile_throttle` and `bundled_job_installs` (no `updated_at`/CAS - insert/upsert-only per
  design.md Non-Goals). Migrate every write path in the research doc's Section 2 table to bump
  `updated_at`; migrate `jobs`/`job_state` writers (`replace_job`, `set_enabled`, `rename_job`,
  `save_all_state`, `set_in_flight`, `clear_in_flight`, `clear_suspended`, `record_success`,
  `record_failure`, `record_manual_failure`) to `cas_update()`. Verify: one test per write path
  asserting `updated_at` changes; a CAS-loss test for at least `replace_job` and
  `record_failure` (two concurrent updates to the same job).
- [ ] 4.3 `sessions.db`: add `updated_at`+`version` to `sessions`; add `created_at` to
  `doctor_mutes` (no `updated_at` - insert/delete-only); add `created_at` to `session_tags`,
  `install_sync`, `context_overrides` (already have `updated_at`, gaining `created_at` for
  symmetry per design.md Goals). Migrate `touch_last_opened`/`touch_last_active` to bump
  `updated_at` and use `cas_update()` (guard against a fork disambiguation-style race where two
  concurrent hook invocations touch the same `(project_dir, basename)` row - `ensure_session_row`
  stays `DO NOTHING`, no CAS needed since it never overwrites). Verify: write-path tests per the
  research doc's Section 3 table; a CAS-loss test for `touch_last_opened`/`touch_last_active`.
- [ ] 4.4 `telemetry.db`: confirmed no changes needed (design.md Non-Goals - pure append-only
  event tables, existing `ts` column already serves as `created_at`). Verify: no code change;
  note this explicitly in the PR description rather than silently skipping.
- [ ] 4.5 `command-cache.db`: add `created_at`+`version` to `command_cache` (`validated_at`
  already functions as `updated_at`, kept as-is per design.md); migrate `cache_record` to use
  `cas_update()` for its `ON CONFLICT DO UPDATE` branch, handling a CAS loss by re-reading and
  retrying once (last-write-wins semantics, matching this table's existing behavior) rather than
  raising - inside the existing `try/except sqlite3.Error` boundary per design.md's Risk note.
  Verify: a test forcing a CAS loss on `cache_record` asserts it retries and succeeds rather than
  silently dropping the fire count update or raising out of the "never raise from cache" boundary.
- [ ] 4.6 Full test suite green after all 5 stores' changes. Verify: `uv run pytest -q`.

## 5. Migration markers (ccmsg, ccsched, sessions) + doctor rewiring

- [ ] 5.1 Append `db.MIGRATIONS_DDL` to each of the three stores' `_DDL`/`DDL` constants; define
  a marker-name constant per store (matching `telemetry_store.LEGACY_JSONL_MIGRATION`'s naming
  style). Verify: a fresh DB for each store has a `migrations` table.
- [ ] 5.2 Locate each store's one-shot legacy-data migration entry point (mirroring
  `cli/migrate_telemetry.py`) and add `db.record_migration()` inside the same transaction as its
  writes, immediately before commit - and `db.migration_applied()` as the refuse-if-already-run
  guard at the top, matching `migrate_telemetry.py`'s `_already_imported`/refusal shape exactly.
  Verify: one test per store - running the migration twice only writes data once; the marker is
  present after a successful run.
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
