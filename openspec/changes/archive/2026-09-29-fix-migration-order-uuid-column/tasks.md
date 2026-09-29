## 1. Baseline tests (red first)

- [x] 1.1 Add `tests/test_sessions_db_migration_chain.py` with the two literal pre-3.0.0 schema builders (v1.0.0-v2.12.x, v2.13.0-v2.14.x, copied from git history) and named baseline-contract tests; correct `_seed_old_schema_db`'s docstring in `tests/test_migrate_sessions_db.py`; verify the import-first test, parametrized over "transcript resolvable" and "no transcript", fails on current code with `no column named uuid` / `no such column: uuid` (red)
- [x] 1.2 Add tests for: standalone import refusal (schema/row-count/`sqlite_master` snapshots unchanged, no backup, dry-run too, corrupt file clean exit, missing file/table unaffected); `migrate all` ordering, refused-rebuild skip, other steps still run (ccmsg/ccsched/telemetry step functions stubbed, never real default paths), closing text; migrate-uuid missing column named, no-`updated_at` dry-run/write, no-`sessions`-table, uuid-keyed-no-marker with a fork; doctor wording and same-path behavior with `CCST_SESSIONS_DIR` set; verify each fails for the right reason on current code

## 2. Implementation

- [x] 2.1 Add the `sessions` structure check and marker-only/missing-table outcomes plus the optional-`updated_at` legacy read; verify the migrate-uuid tests pass
- [x] 2.2 Add the import guard (before any connect/write, dry-run included) and the `migrate all` sequencing and closing text; verify the import and `migrate all` tests pass
- [x] 2.3 Unify doctor on one `sessions_db_path`, add `legacy_sessions_pending` to the 3.0.0 check and update both reasons (both failing branches); verify doctor tests pass and pre-existing doctor tests are unchanged

## 3. Release

- [x] 3.1 Add the CHANGELOG entry and bump to 3.9.2 in `pyproject.toml`, run `uv lock`; verify `uv run pytest -q`, `openspec validate fix-migration-order-uuid-column --strict`, and the repo's lint/type checks all exit 0
- [x] 3.2 End-to-end check in a sandbox (`HOME`, `CCST_SESSIONS_DIR`, `CCST_DATA_HOME`, `CCST_NO_AUTO_SYNC=1`) against the v1.0.0 seed: `sessions migrate` refuses, `migrate all` succeeds, `doctor --no-pypi` shows both sessions checks OK
