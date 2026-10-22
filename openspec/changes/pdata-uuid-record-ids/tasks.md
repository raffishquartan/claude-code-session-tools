# Tasks

## 1. Format marker and schema

- [x] 1.1 Add the `pdata-uuid-ids` marker constant, a format probe (`records.id` declared TEXT = format 4, INTEGER = format 3) and a `pdata_format(conn)` helper in `lib/pdata/`; new databases are created format 4 (`records.id TEXT PRIMARY KEY`, `ext_*.record_id TEXT`, `pdata_verify_watermark.record_id TEXT`). Verify: unit tests for the probe on fresh, format 3 and format 4 databases.
- [x] 1.2 Add the format check used by every record-touching verb: format 3 refuses with a message naming `ccst pdata migrate-uuid`; `migrate-uuid`, `sync-check` (status), `verify` and `doctor` are exempt. Verify: CLI tests run each verb against a format 3 fixture and assert refusal, no writes, and the exempt verbs still run.

## 2. Record ids in the service and CLI

- [x] 2.1 Generate `uuid4` ids in `insert_base_record` and `service.add_record`; change `record_id` and `Record.id` types to `str`; update `init_service` rollback lists and output. Verify: `tests/pdata/test_repository.py` and `test_service.py` updated; add/get round trip asserts a canonical UUID.
- [x] 2.2 Replace `type=int` on `get/update/delete/restore --id` with a UUID validator that fails as a usage error naming the value; same for `resolve --choice <uuid>=local|dump`. Verify: tests for `--id 12`, a valid-but-absent UUID (record not found), and a bad `--choice`.
- [x] 2.3 Replace `ORDER BY id` in `list_base_records`, `query_records` and resolve diff ordering with `created_at, id`; JSON output emits string ids. Verify: ordering test with ids that sort against creation order; json output test.
- [x] 2.4 Update the `ccst pdata` help text (`ID=local|dump` becomes `UUID=local|dump`). Verify: help-output test.

## 3. Deterministic ids

- [x] 3.1 Add the namespace constant and `deterministic_record_id(project, key)` helper; give the session-output watermark and each registered session-output record deterministic ids (`session_output.py`), and make registering an already-registered file a no-op by id. Verify: tests that two independent databases produce identical ids for the same watermark and file, and no duplicate on re-register.

## 4. Dump format and sync

- [x] 4.1 Add the `-- pdata_format=` header; write format 4 dumps and archives to `.pdata-db-dump/v4/` and never touch format 3 files; reads treat a missing header as format 3. Verify: `tests/pdata/test_dump.py` for header, location and format 3 files byte-identical after a publish.
- [x] 4.2 Gate `rehydrate`, adopt-from-dump, `sync_check` and the sync hook on a format match: mismatch is a "migration pending" outcome (not FORK, not an error), names both formats and the command, and changes nothing. Verify: tests in `test_rehydrate.py`, `test_sync_check.py`, `test_pdata_sync_hook.py`, and `test_ccst_pdata_init_cli.py` for adoption.
- [x] 4.3 Replace the integer-collision branch in `resolve._classify`/`apply_resolution` with the UUID semantics (same id = same record; edit conflict, delete-versus-update, group mismatch retained), remove the out-of-band message; update tests in `tests/pdata/test_resolve.py` (`:83,168,173-209,544,574-613`). Verify: rewritten tests, including a fork where each side added different records.

## 5. `ccst pdata migrate-uuid`

- [ ] 5.1 Implement the migration module: plan builder shared by dry run and write; precondition checks (format 3, converged with the format 3 dump, `occupancy.is_occupied` clear, lock probe) each with its own message. Verify: tests per refusal in the spec (unpublished changes, fork, running claude, locked, no database).
- [ ] 5.2 Implement the rewrite: backup via `db.backup_to` into `migration-backups/pdata-<project>-pre-4.0.0-<stamp>.db` and open-check it; single `BEGIN IMMEDIATE` rebuild of `records`, every `ext_*` and `pdata_verify_watermark` with the uuid5 mapping (live and soft-deleted rows); row-count and `verify` checks; marker; publish the format 4 dump with the format 3 vector in the header. Verify: tests for mapping determinism across two copies, soft-deleted rows, per-group counts, backup contents, failure rollback leaves the original.
- [ ] 5.3 Implement idempotency/resume and the joining-machine path (format 4 dump present: check the recorded vector, adopt or refuse with the re-entry message). Verify: tests for second run, resume after a missing marker or dump, matching and non-matching lineages.
- [ ] 5.4 Wire the CLI (`--project/--all-projects`, `--dry-run/--write`, default dry run; dry run lists suspicious `*_id`/`ref` fields). Verify: CLI tests for flags, exit codes and output.
- [ ] 5.5 End-to-end rehearsal in a sandbox with two simulated machines sharing one dump directory: converge on 3.x state, migrate A, publish, migrate B, assert identical ids, then add a record on each and sync without a fork. Verify: scripted test or recorded rehearsal output in the PR.

## 6. Doctor, hook and docs

- [ ] 6.1 Add doctor check `migration-to-4.0.0:pdata-uuid` per project and report unmigrated projects through the `pending-migration` hook (respecting `doctor_mutes`). The FAIL message includes the runbook location (the repository-relative path `docs/pdata-uuid-migration-runbook.md`). Verify: `tests/test_ccst_doctor.py` and hook tests for OK, FAIL, marker/format mismatch.
- [ ] 6.1a Extend `install-everything.sh` to run `ccst pdata migrate-uuid --all-projects` (dry run) after `ccst doctor` and print the framed banner, project list, exact commands and runbook path when any project is format 3; no banner otherwise. Verify: shell test with a format 3 and a format 4 fixture HOME asserting banner present/absent and that the script's exit status is unchanged.
- [ ] 6.2 Rewrite skill text that describes integer ids or the id-collision case: `pm-pdata-resolve-conflicts` (`:34,47,103-120,134-135`), `pm-pdata-do-init` (`:157,250`), `pm-pdata-do-update-project-docs` (`:68`), `pm-pdata-do-update-consuming-skills` (`:80,85`), `pm-pdata-do-manual-migration` (`:92`); update the pdata sync design doc's dump-format section. Verify: skill tests (`test_pdata_*_skill.py`) pass and a grep for "id collision" and "integer id" in skills returns nothing stale.
- [ ] 6.3 Write the committed runbook `docs/pdata-uuid-migration-runbook.md`, a terminal-only, copy-paste, per-machine checklist the user follows with no `claude` session available (ccst upgraded, databases not yet): (a) a one-time prerequisites and "what you will see" section (why record commands and sync refuse until migrated); (b) a machine-order table (which machine goes first and why, and the rule for later machines); (c) numbered steps with the exact shell commands and the expected output or exit status for each - `ccst --version`, `ccst doctor`, `ccsched list` / `ccsched disable <job>` for the pdata jobs, `ccst pdata sync-check --all-projects` until every project is "unchanged", the fork-resolution pointer, `ccst pdata migrate-uuid --all-projects` (dry run), `... --write`, `ccst pdata verify --all-projects`, `ccst doctor`, `ccsched enable <job>`; (d) a "first machine" section and a "each later machine" section, so the same page is followed once per computer; (e) a troubleshooting table keyed by message (unpublished changes, fork, running claude, locked database, lineage mismatch) and the rollback steps; (f) a final checklist. Every command must work from a plain terminal using only exempt verbs (D7) and `ccsched`. Verify: the doc exists at that path and is committed; a test checks it names each command above and the install banner references its path; every command is exercised in the 5.5 rehearsal.

## 7. Release

- [ ] 7.1 Bump `pyproject.toml` to 4.0.0 and add the CHANGELOG entry (Keep a Changelog; **BREAKING** items for ids, dump location, CLI argument types) with the compare links. Verify: `pip show`/`ccst --version` shows 4.0.0 after install; CHANGELOG renders the new section.
- [ ] 7.2 Run every configured check (tests, mypy strict, lint, format) fresh and report exit codes. Verify: all exit 0.

## Workflow follow-up

- Run `openspec-sync-specs` and `openspec-archive-change` before shipping so `openspec/specs/` reflects the merged behaviour.
- Push the branch, open the PR, run `do-code-review`, post findings; stop at the PR URL (no merge unless asked).
