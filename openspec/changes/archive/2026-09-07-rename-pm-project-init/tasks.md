## 1. Rename `pm-project-init` → `pm-pdata-do-init`

- [x] 1.1 `git mv src/cc_session_tools/skills/pm-project-init src/cc_session_tools/skills/
      pm-pdata-do-init`; verify the new directory exists with `SKILL.md` inside and the old one is
      gone.
- [x] 1.2 Update the renamed `SKILL.md`'s `name:` frontmatter field, description/trigger-phrase
      text, and every self-reference to its own old name inside the body; verify by reading the
      file back.

## 2. Update cross-references in other currently-live files

- [x] 2.1 Update every mention of `pm-project-init` in `src/cc_session_tools/skills/
      pm-pdata-do-manual-migration/SKILL.md`; verify by re-grepping the file (expect zero matches).
- [x] 2.2 Update every mention of `pm-project-init` in `src/cc_session_tools/skills/
      pm-pdata-do-audit-and-prepare-to-migrate/SKILL.md`; verify by re-grepping.
- [x] 2.3 Update the mention of `pm-project-init` in `src/cc_session_tools/skills/
      pm-project-layout-reference/SKILL.md` (the file itself is not renamed - only its reference
      to the other skill's new name); verify by re-grepping.
- [x] 2.4 Update the docstring/comment mentions of `pm-project-init` in
      `src/cc_session_tools/lib/pdata/manifest.py`, `classify.py`, and `importers.py` (comment
      text only, no behavior change); verify by re-grepping all three files. Also caught by the
      task 2.6 sweep and fixed here: `cutover.py` (comment) and `init_service.py` (a real
      user-facing dry-run report string, not just a comment - "see pm-project-init's
      garbled-CSV-header caveat") both referenced the old name too, missed by this task's
      original file list drafted during proposal writing.
- [x] 2.5 Update the docstring/comment mentions of `pm-project-init` in
      `tests/pdata/test_init_service.py` and `tests/pdata/test_importers.py` (comment text only -
      neither asserts the literal skill directory name, confirmed during proposal drafting);
      verify by re-grepping both files.
- [x] 2.6 Grep the whole repo once more for `pm-project-init`, excluding
      `openspec/changes/archive/**`, `docs/superpowers/**`, `CHANGELOG.md`'s existing entries, and
      the two live main specs (handled separately in the archive/sync step, task 4) - confirm
      nothing currently-live was missed.

## 3. Full verification and release

- [x] 3.1 Run `uv run pytest -q` and confirm it exits 0.
- [x] 3.2 Update `CHANGELOG.md` with a new entry naming the rename, and bump `pyproject.toml`'s
      `version` to the next patch release (3.3.1 → 3.3.2, continuing the prior change's precedent
      and Chris's explicit instruction); run `uv lock` and commit the regenerated `uv.lock` in the
      same commit.
