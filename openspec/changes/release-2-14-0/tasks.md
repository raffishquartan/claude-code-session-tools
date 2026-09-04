## 1. Manifest rename + backward-compatible resolver

- [ ] 1.1 In `init_paths.py`: rename `PROPOSAL_FILENAME` to `.pdata-migration-manifest.json`; add
  `LEGACY_PROPOSAL_FILENAME = ".ccst-pdata-proposal.json"`; add
  `resolve_proposal_path(project_root: Path) -> Path` (new-name path if it exists, else
  legacy-name path if that exists, else the new-name path). Verify: a test per branch (new
  exists, legacy exists, neither exists, both exist -> new wins).
- [ ] 1.2 Switch every `.exists()`-based call site identified in the research
  (`init_service.py:373`, `manifest.py:123`, `rename_group.py:77,111`, `verify.py:116`) to call
  `resolve_proposal_path()` instead of building `project_root / PROPOSAL_FILENAME` directly.
  Verify: existing pdata test suite passes unchanged; add a regression test proving a project
  with only the legacy-named manifest still round-trips through `init`, `verify`, and
  `rename-group`.
- [ ] 1.3 Fix the one non-constant literal duplicate: `cli/ccst.py:2975`'s argparse help text for
  `rename-group --write`. Update docstring/comment mentions in `verify.py`, `rename_group.py`,
  `init_paths.py`, `classify.py` for consistency (non-functional, no test needed).
- [ ] 1.4 Update `skills/pm-project-init/SKILL.md`'s prose references (lines 39, 65) to the new
  filename, and add a sentence establishing this file as permanent tool state (not a draft) in
  both the skill and the `--write` success message flow. Verify:
  `grep -rn "\.ccst-pdata-proposal\.json" src/` returns only the `LEGACY_PROPOSAL_FILENAME`
  constant definition and the backward-compat fallback logic - no other live reference.

## 2. New-project folder scaffolding

- [ ] 2.1 Write failing tests for `resolve_project_root()`/`init_service.write()`: a project
  whose root did not exist gets `correspondence/`, `meetings-and-calls/`, `workstreams/` created
  alongside it; a project whose root already existed gets none of them, even if none currently
  exist; a dry-run (no `--write`) never creates any of them regardless of root existence. Verify:
  tests fail (behavior doesn't exist yet).
- [ ] 2.2 Implement: capture whether the project root existed before `resolve_project_root()`'s
  `mkdir` call; in `init_service.write()` only (not `dry_run()`), scaffold the three subfolders
  when the root did not previously exist. Verify: 2.1's tests pass.
- [ ] 2.3 Full pdata test suite green, confirming no existing project's re-run of `pdata init`
  gains new folders. Verify: `uv run pytest -q -k pdata`.

## 3. Verify hardening: independent migration-evidence checks

- [ ] 3.1 Write failing tests for `check_row_count_parity()`: manifest absent + no
  `.pdata-migrated/` content + no populated record group -> `[]` (unchanged); manifest absent +
  `.pdata-migrated/<group>/` has content -> one FAIL issue naming "manifest now missing"; manifest
  absent + a record group has rows but no `.pdata-migrated/` content -> same FAIL issue (either
  signal alone is sufficient evidence); manifest present -> existing parity-check behavior
  unchanged. Verify: tests fail against current code.
- [ ] 3.2 Implement: before the current `if not proposal_path.exists(): return []` early return
  (now keyed off `resolve_proposal_path()` per task 1.2), add the two independent checks
  (`.pdata-migrated/` directory has any entries; `repository.list_record_groups(conn)` has any
  group with a non-empty `records` table) and raise the new FAIL issue when either is true, with
  a message naming `ccst pdata schema show`/`ccst pdata schema list` as recovery-inspection
  commands. Verify: 3.1's tests pass.
- [ ] 3.3 Full pdata verify test suite green. Verify: `uv run pytest -q -k pdata_verify` (or the
  equivalent test module).

## 4. Bundle `pm-pdata-audit` and `pm-pdata-migrate`

- [ ] 4.1 Copy `~/.claude/skills/pm-pdata-audit/` and `~/.claude/skills/pm-pdata-migrate/` into
  `src/cc_session_tools/skills/` in this repo, verbatim (research confirmed both are already
  portable - no personal/project-specific content to scrub). Verify:
  `ls src/cc_session_tools/skills/pm-pdata-audit/SKILL.md src/cc_session_tools/skills/pm-pdata-migrate/SKILL.md`.
- [ ] 4.2 Confirm `_discover_skills()` picks both up with no code change (directory-scan
  mechanism, no registry to update) and that `ccst skills install`/`install-everything --apply`
  successfully symlinks both in a test/dry-run install. Verify: an existing or new
  `test_ccst_skills`-style test asserting both names appear in the discovered/installed skill
  list.
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
