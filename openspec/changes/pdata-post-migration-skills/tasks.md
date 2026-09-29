## 1. New skills

- [ ] 1.1 Create `pm-pdata-do-update-project-docs/SKILL.md` and verify its tests (2.1) pass
- [ ] 1.2 Create `pm-pdata-do-update-consuming-skills/SKILL.md` and verify its tests (2.1) pass
- [ ] 1.3 Add a post-`--write` step to `pm-pdata-do-init/SKILL.md` invoking both skills in order and verify the test in 2.2 passes

## 2. Tests

- [ ] 2.1 Add tests asserting both skills are discovered, have correct frontmatter names, and contain the spec's key requirements (cwd check, cross-check before stale, layout drift report-only, literal-path only, direct/passing classification, human-decision flag, fresh-context subagent)
- [ ] 2.2 Add a test that `pm-pdata-do-init/SKILL.md` names both skills

## 3. Remove prompts, update CLI

- [ ] 3.1 Replace `_discover_prompts_dir` and `_print_migration_prompt_reminders` in `ccst.py` with `_print_post_migration_skill_reminders`; update the two `pdata init` call sites; verify `tests/test_ccst_pdata_init_cli.py` (updated to assert skill names, project path, one fresh-context line per skill, and no prompt filenames) passes
- [ ] 3.2 Delete `src/cc_session_tools/prompts/` and its `pyproject.toml` package/exclude entries; remove prompts-directory tests from `tests/test_ccst_bundle_discovery.py` and drop `prompts` from the simulated installed layout; verify the file's tests pass
- [ ] 3.3 Verify `grep -rn "pdata-migration-" src tests pyproject.toml` finds nothing

## 4. Release

- [ ] 4.1 Add a CHANGELOG entry (Keep a Changelog) and bump `pyproject.toml` to 3.10.0 (and `uv.lock` if it records the version); verify the full test suite passes
