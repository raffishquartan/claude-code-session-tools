## Why

Following up on `rename-pdata-skills-batch1` (PR #141, stacked on #140): Chris wants
`pm-project-init` renamed to `pm-pdata-do-init` for two reasons - it should start with the
`pm-pdata-` prefix like every other skill that drives `ccst pdata` directly, and the family should
read imperatively (matching `pm-pdata-do-audit-and-prepare-to-migrate` and the just-renamed
`pm-pdata-do-manual-migration`). Kept as a separate, later-stacked change from the batch-1 renames
because it is the most cross-referenced of the four skills and the riskiest to get wrong.

## What Changes

- **BREAKING** (skill identity): rename `pm-project-init` → `pm-pdata-do-init` - directory,
  `name:` frontmatter, and every self-reference inside its own `SKILL.md`. No compatibility alias
  for the old `/pm-project-init` slash command, matching the batch-1 renames' precedent.
- Every currently-live cross-reference to `pm-project-init` by name is updated: the other bundled
  `pm-*` SKILL.md files that name it (`pm-pdata-do-manual-migration`,
  `pm-pdata-do-audit-and-prepare-to-migrate`, `pm-project-layout-reference`), source docstrings in
  `src/cc_session_tools/lib/pdata/` that cite it for context, the bundled-skills discovery test,
  and the two live main specs that name it (`cli/bundled-pm-skills`,
  `pdata/migration-guidance`).
- `pm-project-layout-reference` is explicitly untouched (out of scope, per Chris's instruction in
  the prior change).
- Historical records are left untouched, matching `rename-pdata-skills-batch1`'s own precedent:
  past `CHANGELOG.md` entries, `openspec/changes/archive/**`, and `docs/superpowers/**` planning
  docs describe what was true when written.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `cli/bundled-pm-skills`: the requirement's own description text cites `pm-project-init` by name
  in a scenario ("the same way `pm-project-init`... are") - updated to the new name.

## Impact

- Skill directory: `src/cc_session_tools/skills/pm-project-init/` →
  `src/cc_session_tools/skills/pm-pdata-do-init/`.
- Cross-references: `src/cc_session_tools/skills/pm-pdata-do-manual-migration/SKILL.md`,
  `src/cc_session_tools/skills/pm-pdata-do-audit-and-prepare-to-migrate/SKILL.md`,
  `src/cc_session_tools/skills/pm-project-layout-reference/SKILL.md` (cites it, file itself stays
  un-renamed), `src/cc_session_tools/lib/pdata/manifest.py`, `src/cc_session_tools/lib/pdata/
  classify.py`, `src/cc_session_tools/lib/pdata/importers.py` (docstring mentions only).
- Tests: `tests/pdata/test_init_service.py`, `tests/pdata/test_importers.py` (docstring/comment
  mentions only - verified before editing whether any assert the literal skill name).
- Specs: `openspec/specs/cli/bundled-pm-skills/spec.md`, `openspec/specs/pdata/migration-guidance/spec.md`.
- Per this repo's version policy this is a patch bump (3.3.1 → 3.3.2), continuing
  `rename-pdata-skills-batch1`'s precedent and explicit instruction for this pair of changes.
