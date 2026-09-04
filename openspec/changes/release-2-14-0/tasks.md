## 1. Manifest rename + backward-compatible resolver

- [x] 1.1 In `init_paths.py`: rename `PROPOSAL_FILENAME` to `.pdata-migration-manifest.json`; add
  `LEGACY_PROPOSAL_FILENAME = ".ccst-pdata-proposal.json"`; add
  `resolve_proposal_path(project_root: Path) -> Path` (new-name path if it exists, else
  legacy-name path if that exists, else the new-name path). Verify: a test per branch (new
  exists, legacy exists, neither exists, both exist -> new wins) in `test_init_paths.py`.
- [x] 1.2 Switch every `.exists()`-based call site identified in the research
  (`init_service.py` dry_run + write, `rename_group.py` `_plan` + `_rename_in_manifest`,
  `verify.py`'s `check_row_count_parity`) to call `resolve_proposal_path()` instead of building
  `project_root / PROPOSAL_FILENAME` directly (`manifest.py:load_or_create` needed no change -
  it already takes `proposal_path` as a parameter). Also fixed `classify.py`/`reorganize.py`'s
  own-bookkeeping exclusion sets, which only excluded the new name and would have
  misclassified/flagged a legacy-named project's manifest as project content. Verify: existing
  pdata test suite green; a regression test per module proving a project with only the
  legacy-named manifest still round-trips through `init`, `verify`, and `rename-group`
  (`test_row_count_parity_resolves_legacy_manifest_name` etc.).
- [x] 1.3 Fixed the one non-constant literal duplicate: `cli/ccst.py`'s `rename-group --write`
  argparse help text now interpolates `init_paths.PROPOSAL_FILENAME`. Updated docstring/comment
  mentions in `rename_group.py`'s module docstring for consistency (non-functional, no test
  needed).
- [x] 1.4 Updated `skills/pm-project-init/SKILL.md`'s prose references to the new filename, with
  a sentence establishing this file as permanent tool state (not a draft) and naming the legacy
  fallback. Verify: `grep -rn "\.ccst-pdata-proposal\.json" src/` returns only the
  `LEGACY_PROPOSAL_FILENAME` constant definition and the two intentional SKILL.md
  legacy-fallback mentions - no other live reference. Also swept every hardcoded
  `.ccst-pdata-proposal.json` test-fixture literal across the whole test suite (found beyond the
  4 call sites the research scoped) to use `init_paths.PROPOSAL_FILENAME` instead, since several
  broke outright once the constant's value changed.

## 2. New-project folder scaffolding

- [x] 2.1-2.2 **Hook point moved from `write()` to `dry_run()` during implementation** (see
  design.md Decision 1's revision): in the normal CLI flow `dry_run()` always runs first and
  already creates the project root as an accepted existing side effect, so a newness check
  inside `write()` would never fire in real usage - by the time `--write` runs, the root the
  preceding dry-run created already exists. Added `init_paths.project_root_exists_already()`
  (checked before `resolve_project_root()`'s own `mkdir`) and
  `init_paths.scaffold_new_project_dirs()`; `dry_run()` calls both, gated on newness, before its
  own `resolve_project_root()` call. `write()` is unaffected. Verify: tests in
  `test_init_paths.py` (new/existing/rehearsal newness detection, scaffold idempotency) and
  `test_init_service.py` (`test_dry_run_scaffolds_starting_folders_for_a_genuinely_new_project`,
  `test_dry_run_does_not_scaffold_an_already_existing_project`,
  `test_dry_run_second_call_does_not_rescaffold_or_error` - covers the deleted-folder-stays-
  deleted case too).
- [x] 2.3 Full pdata test suite green, confirming no existing project's re-run of `pdata init`
  gains new folders. Verify: `uv run pytest -q` (full suite, 29 tests in `test_init_service.py`
  alone).

## 3. Verify hardening: independent migration-evidence checks

- [x] 3.1-3.2 Implemented and revised during TDD: manifest absent + no `.pdata-migrated/`
  content -> `[]` (unchanged); manifest absent + `.pdata-migrated/<group>/` has content -> one
  FAIL issue naming "manifest now missing"; manifest present -> existing parity-check behavior
  unchanged. **Row counts dropped as an independent trigger** (see design.md Decision 3's
  revision) - running the existing test suite immediately showed false positives against every
  project fixture that uses `pdata add` directly without ever running `ccst pdata init`, since
  populated record groups alone can't distinguish that from a genuine lost-manifest case.
  `.pdata-migrated/` presence (confirmed written by exactly one code path, `cutover.py`) is the
  only reliable independent signal. Implemented in
  `_check_manifest_missing_with_evidence()`, called from `check_row_count_parity()`'s early
  return (now keyed off `resolve_proposal_path()` per task 1.2), with a message naming
  `ccst pdata schema show`/`ccst pdata schema list` as recovery-inspection commands.
- [x] 3.3 Full pdata verify test suite green. Verify: `uv run pytest -q tests/pdata/test_verify.py
  tests/test_ccst_pdata_verify_cli.py` (30 + 38 passing, including 4 new tests covering the
  `pdata add`-only false-positive fix, the archive-evidence FAIL case, and legacy-manifest
  resolution).

## 4. Bundle `pm-pdata-audit` and `pm-pdata-migrate`

- [x] 4.1 Copy `~/.claude/skills/pm-pdata-audit/` and `~/.claude/skills/pm-pdata-migrate/` into
  `src/cc_session_tools/skills/` in this repo, verbatim (research confirmed, and a full read
  before committing re-confirmed, both are already portable - no personal/project-specific
  content to scrub).
- [x] 4.2 Confirmed `_discover_skills()` picks both up with no code change (directory-scan
  mechanism, no registry to update) and that `ccst skills install --apply` successfully symlinks
  both into a real target directory. Verify:
  `test_pm_pdata_audit_and_migrate_are_bundled_skills` in `test_ccst_bundle_discovery.py`; manual
  `ccst skills install --target <tmp> --apply` confirmed both linked correctly.
- [ ] 4.3 Once this task's changes are committed on this branch, send a `ccmsg` message to
  `project=claude-code-config-sync` (per task #7's description) noting that CCST now bundles
  `pm-pdata-audit`/`pm-pdata-migrate`, so that repo's own sync/drift handling accounts for them.

## 5. Release

- [ ] 5.1 Full test suite green end-to-end. Verify: `uv run pytest -q`.
- [ ] 5.2 Update `CHANGELOG.md` for `[2.14.0]` (Added: new-project folder scaffolding, manifest
  permanence + rename with legacy fallback, verify's independent migration-evidence checks,
  `pm-pdata-audit`/`pm-pdata-migrate` bundling). Commit.
- [ ] 5.3 Bump `pyproject.toml` version to `2.14.0`; run `CCST_NO_AUTO_SYNC=1 uv lock`; commit
  both together (follow-on commit per this repo's convention).
- [ ] 5.4 Sync specs (`pdata/init-scaffolding`, `pdata/manifest-naming`, `pdata/verify-integrity`,
  `cli/bundled-pm-skills`) and archive this change.
- [ ] 5.5 Push `f/2.14.0`, open a PR (base `main`) - recommend title/body, confirm with the user
  before running `gh pr create`.
