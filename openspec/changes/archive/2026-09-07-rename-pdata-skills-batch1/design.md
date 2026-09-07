## Context

See proposal.md - Why. This branches from the tip of `f/20260907-improve-pdata-migration-workflow`
(PR #140, still open) rather than `main`, per this repo's stacking convention - `main` doesn't yet
have that PR's `pm-project-init`/`pm-pdata-do-migrate` deprecation-gate content, and this rename
needs to land on top of it. `openspec/changes/archive/2026-09-07-improve-pdata-migration-workflow/`
and `openspec/specs/pdata/migration-guidance/spec.md` already exist on this branch as a result.

`_discover_skills` (`src/cc_session_tools/cli/ccst.py`) is a pure directory scan - any subdirectory
of the bundled `skills/` with a `SKILL.md` is discovered and installed. There is no separate
registry to update; renaming a directory is, mechanically, the entire "bundling" change. Everything
else in this change is making prose/test references match the new names.

## Goals / Non-Goals

**Goals:**
- Rename three skill directories and every currently-live reference to them.
- Fix the pre-existing `cli/bundled-pm-skills` main-spec drift (still named the pre-3.1.0
  identifiers) while touching that file anyway.

**Non-Goals:**
- No compatibility alias/symlink for the old names - matches how this repo has renamed skills
  before (`pm-pdata-migrate` → `pm-pdata-do-migrate` in 3.1.0, no alias kept).
- No edits to historical record: past `CHANGELOG.md` entries, anything under
  `openspec/changes/archive/**`, or `docs/superpowers/plans/**`/`docs/superpowers/specs/**`
  planning docs. These describe what was true when written.
- `pm-project-init` is not renamed here - that's a separate, stacked change
  (`rename-pm-project-init`), by Chris's explicit instruction to keep it isolated.

## Decisions

### Rename mechanically via `git mv`, not delete+recreate
Preserves file history across the rename for `git blame`/`git log --follow`. Each skill's
`SKILL.md` content is then edited in place for its own self-references and its `name:`
frontmatter field.

### Update cross-references by grep, scoped to currently-live files only
`grep -rl "<old-name>"` across the repo, then filter to files that are current source of truth:
other bundled `SKILL.md` files, active `src/`/`tests/` code, and `openspec/specs/**` (main specs,
not archived changes). Anything under `openspec/changes/archive/**`, `docs/superpowers/**`, or a
past `CHANGELOG.md` entry is left untouched as historical record (see Non-Goals).

### `pdata/migration-guidance` main spec needs updating too
That spec (created by the just-archived `improve-pdata-migration-workflow` change) has several
requirement bodies naming `pm-pdata-do-migrate` literally - e.g. "`pm-pdata-do-migrate`'s `SKILL.md`
SHALL state...". Since it's a live main spec (not historical record), its prose must track the
actual current skill name. Handled as a MODIFIED delta here alongside the `cli/bundled-pm-skills`
one, since the requirement descriptions themselves need no other change beyond the name swap.

## Risks / Trade-offs

- [Risk] Anyone with `/pm-pdata-do-migrate`, `/pm-pdata-conflict-resolution`, or
  `/pm-pdata-schema-design` muscle memory hits "unknown command" after this ships and a reinstall
  runs. → Mitigation: this is Chris's own explicit, deliberate choice (no alias wanted); the
  CHANGELOG entry names all three old→new mappings clearly for reference.
