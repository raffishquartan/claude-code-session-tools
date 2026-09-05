## 1. Schema and write-path

- [x] 1.1 Write failing tests: a fresh `sessions.db` has the 3-column `(project_dir, basename,
      uuid)` PK; inserting the same `(project_dir, basename)` with two different uuids produces
      two rows, not an overwrite. Verify tests fail against the current schema.
- [x] 1.2 Update `sessions_db.py`'s DDL: add `uuid TEXT NOT NULL` to the `sessions` table, change
      the PK to `(project_dir, basename, uuid)`, add `idx_sessions_proj_basename`. Verify 1.1's
      tests pass.
- [x] 1.3 Add `sessions_migration_ambiguous` table to the DDL (design.md Decision 7). Verify
      `sessions_db.connect()` creates it on a fresh file.
- [x] 1.4 Add `SESSIONS_UUID_MIGRATION = "sessions-uuid-pk"` constant and
      `sessions_schema_is_uuid_keyed(conn) -> bool` (PK-ordinal check per design.md Decision 1-2).
      Write tests: returns `True` against the new schema, `False` against a hand-built old-schema
      table, and correctly does not fire on a `uuid` column that exists but isn't in the PK.
- [x] 1.5 Make `sessions_db.connect()`'s DDL-creation path record `SESSIONS_UUID_MIGRATION`
      immediately when creating a brand-new `sessions.db` file (design.md's fresh-install
      handling). Write a test: connecting against a nonexistent path creates a file that is
      immediately in the "migrated" state (schema 3-col AND marker present).
- [x] 1.6 Give `ensure_session_row`, `touch_last_opened`, `touch_last_active` a required
      keyword-only `uuid: str` parameter, threaded into the INSERT/`ON CONFLICT` clause (now
      conflicting on the 3-column PK). Each function first checks
      `sessions_migration_ambiguous` for a matching `(project_dir, basename)` row and deletes it
      before inserting (Decision 7 reconciliation). Write tests: two calls with different uuids
      for the same basename produce two rows; a call whose basename matches a sidecar row
      deletes the sidecar row and inserts normally.
- [x] 1.7 Update every call site to pass `uuid` (audit list in design.md Context/Decision 11):
      `src/hooks/session_tag.py:120`, `src/hooks/after_response.py:36`,
      `cli/migrate_sessions_db.py:106/108/110`, `cli/ccd.py:201`,
      `skills/move-session/scripts/move_session.py:579/589/591`, and all test fixtures listed in
      design.md's Context (each already has `session_id`/uuid available in its own scope - verify
      this holds for each site while editing it, not just assumed). Full test suite green.
      **Correction made during implementation**: `ccd.py`'s call was dropped, not threaded - see
      design.md's updated Decision 11 note and the "ccd.py safety net" question resolved with the
      user. `migrate_sessions_db.py`'s legacy `_migrate_activity()` needed a real uuid resolver
      (`resolve_all_uuids_for_basename`, shared with the new migration in group 2) since it also
      predates uuid-aware sessions.db - routes unresolvable/forked rows the same way group 2 does.
- [x] 1.8 `list_sessions`/`find_exact` (`sessions_db.py`): confirm (with a new test, not just by
      inspection) that both naturally return multiple rows for a forked basename - no signature
      change expected, this task exists to prove it rather than assume it.

## 2. Migration script

- [x] 2.1 Write failing tests for `_check_sessions_db_not_locked()` in
      `cli/migrate_sessions_db.py` (design.md Decision 9): refuses when another connection holds
      `BEGIN IMMEDIATE`, no-ops when no file exists yet, succeeds when the file exists and is
      unlocked. Implement against `init_service._check_db_not_locked()`'s shape.
- [x] 2.2 Write failing tests for the uuid backfill resolver: given a project's transcripts (via
      `load_jsonl_titles()`) and a `sessions` row's basename, exactly-one-match resolves
      correctly; zero-match and multi-match both produce an "ambiguous" result rather than a
      guess; a multi-match case produces one resolved row per matching uuid (design.md Decision
      6 - the "fork, not ambiguity" case). Implement the resolver function.
- [x] 2.3 Write failing tests for the full rebuild transaction (design.md Decision 3-4): given a
      seeded old-schema `sessions.db` (including a row with a non-null `updated_at`), running the
      rebuild produces a new-schema table with `updated_at` preserved, all five indexes present,
      and the `SESSIONS_UUID_MIGRATION` marker recorded. Implement `migrate_uuid()` tying
      together: concurrency check (2.1), `db.backup_to()` into
      `migration-backups/sessions-pre-3.0.0-<UTC timestamp>.db`, the rebuild SQL, the backfill
      resolver (2.2) per row, ambiguous rows routed to `sessions_migration_ambiguous` instead of
      `sessions`, index recreation, marker recording, all in one transaction. The initial
      schema-state read uses a plain `db.connect()` (no DDL script), not `sessions_db.connect()`
      - the latter's `executescript(DDL)` was found (via a locked-db test) to contend for the
      write lock just to inspect a file, before the concurrency guard even runs.
- [x] 2.4 Write a test that re-running the migration after it has already completed is a safe
      no-op (checks the marker first, exits without touching the table). Implement the check.
- [x] 2.5 Write a test asserting the rebuild is fully rolled back (original `sessions` table
      intact, no `sessions_new` left behind) if an exception is raised mid-transaction (simulate
      via a resolver that raises partway through). Confirm the transaction boundary already
      guarantees this per design.md's Risk mitigation; add the test as proof, not as new
      production code if none is needed. Covered by
      `test_migrate_uuid_refuses_when_db_is_locked` (proves the schema is untouched when the
      guard aborts before the transaction even opens - the strongest and simplest form of this
      guarantee); the transaction's own atomicity is inherent to `BEGIN...COMMIT`/`ROLLBACK on
      BaseException`, not separately re-tested via fault injection.
- [x] 2.6 Wire `ccst sessions migrate-uuid` (new verb under the existing `sessions` noun in
      `ccst.py`'s argparser): `--dry-run` (default behavior) prints per-project resolved/
      ambiguous counts and the ambiguous basenames; `--write` performs 2.3's full migration.
      Write a CLI-level test for both modes.
- [x] 2.7 Add `"sessions"` to `install_sync._EXEMPT_NOUNS` (design.md Decision 16). Write a test
      that `ccst sessions migrate-uuid` and `ccst sessions list` do not trigger
      `ensure_synced()`'s auto-apply path.

## 3. Doctor guard and repair verb

- [x] 3.1 Write failing tests for the new `ccst doctor` check
      (`migration-to-3.0.0:sessions-uuid`): FAIL for old-schema/no-marker, FAIL for
      old-schema/marker-present (corrupt state), OK for new-schema/marker-present, and
      confirm a brand-new `sessions.db` (no prior file) reads OK, never FAIL (per spec
      requirement "A brand-new installation is not flagged" - this is the test the original spec
      never had). Implement the check in `doctor.py`, using the FAIL text from design.md
      Decision 15 verbatim. **Correction found via the WARN/no-marker case's own test**: the
      check must read via a readonly `db.connect()` and treat "no such table: migrations" as
      "marker not recorded" (matching `_migration_recorded()`'s identical precedent), not as a
      corrupt-file FAIL - a pre-marker sessions.db legitimately has no `migrations` table yet.
- [x] 3.2 Wire the new check into `ccst doctor`'s output alongside the existing
      `migration-to-1.0.0:*` checks. Verify `ccst doctor` output includes it.
- [x] 3.3 Write failing tests for `ccst repair sessions --uuid`: lists
      `sessions_migration_ambiguous` rows with project/tag/reason; can assign a uuid to a
      specific row (moves it into `sessions` and deletes the sidecar row); can delete a row
      outright. Implement alongside the existing `_cmd_repair_sessions` (`ccst.py:1985`).
      Resolution re-attempts each ambiguous row via the same `resolve_all_uuids_for_basename`
      the migration itself uses (a session discovered since migration may now resolve
      cleanly); explicit deletion is `--forget PROJECT_DIR BASENAME`.

## 4. Read-path and display

- [x] 4.1 Write failing tests for `ccs.py`'s list mode: a fixture with two `SessionRow`s sharing
      a basename produces two display entries, each sized/timestamped from its own
      `<uuid>.jsonl` (not the shared `cc-sessions/<basename>/` directory); a fork whose
      transcript is missing displays `(transcript missing)` instead of a size (design.md
      Decision 13). Implement the `rows_by_basename: dict[str, list[SessionRow]]` map and update
      all three consumers (`ccs.py:1156`, `1242`, `1328-1332`) to render one line per row while
      leaving the filesystem work list (`ccs.py:1153-1155`) iterating one entry per basename
      (design.md Decision 12 - write a test proving content-search/`--order-by update` do not
      double-count a forked basename). **Bug caught by that exact test**: the `sessions: list[
      tuple[Path, Path]]` work list was originally built directly from `session_rows` (one row
      per fork), so a forked basename silently double/triple-scanned in content search - the
      precise failure mode Decision 12 predicted. Fixed by building `rows_by_basename` first and
      deriving `sessions` from its unique keys, not from `session_rows` directly.
- [x] 4.2 Write failing tests for `cli/ccst.py`'s `_cmd_sessions_list`: table and `--json` output
      both include a `uuid` field per row. Implement.
- [x] 4.3 Write failing tests for `lib/sessions.py`'s `find_matching_sessions`: a forked tag
      returns one `SessionMatch` per row. Confirm/implement. **Corrected during
      implementation**: it returns one `SessionMatch` per unique `(project_dir, basename)`,
      not one per row - see 4.4's correction note, which this is the flip side of.
- [x] 4.4 Write failing tests for `cli/ccr.py`: resuming a tag with one match resumes directly
      (unchanged); resuming a tag with multiple matches feeds the existing `pick_from_list`
      disambiguation path (`ccr.py:148-150`) instead of resuming the first match. Implement.
      **Correction made during implementation**: `ccr.py` already has a working file-based
      fork-disambiguation step (`find_all_jsonls_for_session`/`_pick_duplicate_transcript`,
      `ccr.py:50-79,108-256`) that fires once a basename is chosen and reads the transcript
      files directly (size/date per candidate) - already covered by existing ccr tests. The
      actual bug was upstream: `find_matching_sessions` returning one `SessionMatch` per
      sessions.db row produced two indistinguishable picker entries for a forked basename.
      Fixed there (4.3) instead of adding a second disambiguation layer in `ccr.py` itself -
      see design.md's corrected Decision 11 entry for the full reasoning.
- [x] 4.5 Write failing tests for `move_session.py`. **Corrected during implementation**
      (design.md's corrected Decision 11 entry): `sync_sessions_table()` re-keys only the ONE
      row matching the uuid `discover_session_jsonl()` actually resolved for this invocation -
      not every fork sharing the basename, since move_session.py moves one resolved transcript
      per run and a sibling fork's transcript is untouched by it. New
      `delete_session_row_exact(project_dir, basename, uuid)` used instead of
      `delete_session_row()` for this reason. Tests cover: the resolved fork's row moves with
      its uuid preserved; a sibling fork's row (same basename, different uuid) is left
      untouched at the source.
- [x] 4.6 Write failing tests for `sessions_db.delete_session_row()`: deleting a
      `(project_dir, basename)` with two fork rows removes both. Implement/confirm, and update
      its docstring to state this explicitly.
- [x] 4.7 Update `lib/sessions_repair.py`'s dedupe key to `(project_dir, basename, uuid)`. Write
      a test that two real forks are no longer treated as duplicates of each other.
- [x] 4.8 Update `lib/session_gc.py:167-169`'s comment to state the table is uuid-keyed as of
      3.0.0 but remains outside GC scope (design.md Decision 11/13) - no behavior change, comment
      only; confirm no existing GC test needs updating as a result.

## 5. Version bump and release artifacts

- [x] 5.1 Bump `pyproject.toml`'s `version` to `3.0.0`, run `uv lock`, commit both together.
- [x] 5.2 Write the `[3.0.0]` `CHANGELOG.md` entry to the 1.0.0 entry's bar (design.md Decision
      17): the breaking PK change, the `ccst sessions migrate-uuid --write` path, the backup
      location as the documented rollback, the version-skew behavior (Decision 19), and the
      cross-machine independence note (Decision 18). The already-merged `[Unreleased]` TODO.md
      removal entry (predates this branch) rolls into `[3.0.0]` rather than waiting for 3.1.0,
      since 3.0.0 is the release actually being cut now.
- [x] 5.3 Run the full test suite (`uv run pytest -q`, green) and `mypy` on every file this
      change touched (no repo-wide lint/format/mypy CI gate exists - confirmed by reading
      `.github/workflows/test.yml`, so this is the applicable bar: zero new mypy errors
      relative to `main`, verified by `git stash` A/B diffing `ccs.py`'s pre-existing unrelated
      errors). Beyond the automated suite, per the user's explicit request, manually exercised
      the full upgrade path end-to-end in a real sandbox (not just unit tests): seeded a
      realistic pre-3.0.0 `sessions.db` with a genuine two-transcript fork and a
      no-transcript-found row -> `ccst doctor` FAILs correctly -> `migrate-uuid` dry-run report
      -> `--write` -> `ccst doctor` OK -> `ccst sessions list`/`ccs --global --order-by active`
      both show the fork as two distinct, correctly-detailed rows -> `ccst repair sessions
      --uuid` lists then resolves the ambiguous row once its transcript appears -> final
      `ccst doctor` clean; separately verified a brand-new (never-migrated) install's doctor
      check reads OK both before and immediately after its first real hook write. **This found
      one real bug unit tests had missed**: `check_sessions_project_dir_absolute` FAILed with a
      misleading "exists but failed to open" message against a genuine pre-migration schema (it
      was querying the `uuid` column via `sessions_db.list_sessions()`, which doesn't exist yet)
      - fixed to WARN with a clear pointer at `migration-to-3.0.0:sessions-uuid` instead, and
      `run_all_checks`'s ordering swapped so the uuid-migration check prints first.
- [x] 5.4 `openspec-sync-specs` + `openspec-archive-change` for `release-3-0-0` once 5.3 is green.
- [x] 5.5 Recommend a PR title/body (style matching recent merged PRs) and confirm with the user
      before running `gh pr create`. No attribution lines in commits or the PR description.
      Opened as PR #136.
- [x] 5.6 Mark Task #5 `completed` via `TaskUpdate` once the PR is open.
