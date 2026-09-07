## 1. Rename `pm-pdata-do-migrate` → `pm-pdata-do-manual-migration`

- [x] 1.1 `git mv src/cc_session_tools/skills/pm-pdata-do-migrate src/cc_session_tools/skills/
      pm-pdata-do-manual-migration`; verify the new directory exists with `SKILL.md` inside and
      the old one is gone.
- [x] 1.2 Update the renamed `SKILL.md`'s `name:` frontmatter field and every self-reference to
      its own old name inside the file (description, deprecation notice, confirmation-gate text);
      verify by reading the file back.

## 2. Rename `pm-pdata-conflict-resolution` → `pm-pdata-resolve-conflicts`

- [x] 2.1 `git mv src/cc_session_tools/skills/pm-pdata-conflict-resolution src/cc_session_tools/
      skills/pm-pdata-resolve-conflicts`; verify the new directory exists.
- [x] 2.2 Update the renamed `SKILL.md`'s `name:` frontmatter field and self-references; verify by
      reading the file back.

## 3. Rename `pm-pdata-schema-design` → `pm-pdata-design-schema`

- [x] 3.1 `git mv src/cc_session_tools/skills/pm-pdata-schema-design src/cc_session_tools/skills/
      pm-pdata-design-schema`; verify the new directory exists.
- [x] 3.2 Update the renamed `SKILL.md`'s `name:` frontmatter field and self-references; verify by
      reading the file back.

## 4. Update cross-references in other currently-live files

- [x] 4.1 Update every mention of the three old names in `src/cc_session_tools/skills/
      pm-pdata-do-audit-and-prepare-to-migrate/SKILL.md`; verify by re-grepping the file for the
      old names (expect zero matches).
- [x] 4.2 Update every mention of the three old names in `src/cc_session_tools/skills/
      pm-project-init/SKILL.md` (its "Do NOT use for..." cross-references); verify by re-grepping.
- [x] 4.3 Update the mention of `pm-pdata-schema-design` in `src/cc_session_tools/skills/
      pm-project-layout-reference/SKILL.md`; verify by re-grepping.
- [x] 4.4 Update the docstring/comment mention of `pm-pdata-conflict-resolution` in
      `src/cc_session_tools/lib/pdata/resolve.py`, and of `pm-pdata-schema-design` in
      `src/cc_session_tools/lib/pdata/service.py`; verify by re-grepping both files (no behavior
      change - comment text only).
- [x] 4.5 Update `tests/test_ccst_bundle_discovery.py`'s `test_pm_pdata_audit_and_migrate_are_bundled_skills`
      (rename the test function too, to match) to assert the new directory names are discovered;
      verify `uv run pytest tests/test_ccst_bundle_discovery.py -q` passes.
- [x] 4.6 Grep the whole repo once more for all three old names, excluding
      `openspec/changes/archive/**`, `docs/superpowers/**`, and `CHANGELOG.md`'s existing entries
      - confirm nothing currently-live was missed.

## 5. Full verification and release

- [x] 5.1 Run `uv run pytest -q` and confirm it exits 0.
- [x] 5.2 Update `CHANGELOG.md` with a new entry naming all three old→new renames, and bump
      `pyproject.toml`'s `version` to the next patch release (per Chris's explicit instruction for
      this change); run `uv lock` and commit the regenerated `uv.lock` in the same commit.
