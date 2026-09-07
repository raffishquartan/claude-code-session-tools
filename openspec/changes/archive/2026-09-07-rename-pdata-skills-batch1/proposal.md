## Why

Chris wants every `pm-pdata-*` skill named imperatively (matching `pm-pdata-do-audit-and-prepare-to-migrate`
and `pm-pdata-do-migrate`'s style) so the family reads consistently, and wants three names changed to be
clearer about what each skill actually does: `pm-pdata-do-migrate` no longer reads as "the" migration path
now that it's a deprecated fallback (see `openspec/changes/archive/2026-09-07-improve-pdata-migration-workflow/`),
and `pm-pdata-conflict-resolution`/`pm-pdata-schema-design` read more naturally as imperative verb-first names.

## What Changes

- **BREAKING** (skill identity): rename three bundled skills, directory and `name:` frontmatter both:
  - `pm-pdata-do-migrate` → `pm-pdata-do-manual-migration` (clarifies it's the manual/deprecated path,
    distinct from the tool-native `ccst pdata init` route)
  - `pm-pdata-conflict-resolution` → `pm-pdata-resolve-conflicts`
  - `pm-pdata-schema-design` → `pm-pdata-design-schema`
  - A session that types the old `/pm-pdata-do-migrate`, `/pm-pdata-conflict-resolution`, or
    `/pm-pdata-schema-design` slash command after this change ships (and a reinstall runs) gets
    "unknown command" - the new names must be used instead. No compatibility alias is kept, matching
    how skills in this repo have been renamed before (see `pm-pdata-do-migrate`'s own history: renamed
    from `pm-pdata-migrate` in 3.1.0, per `tests/test_ccst_bundle_discovery.py`'s docstring).
- Every cross-reference to these three skills by name - in the other bundled `pm-*` SKILL.md files, the
  `cli/bundled-pm-skills` main spec (already stale: it names the pre-3.1.0 `pm-pdata-audit`/`pm-pdata-migrate`
  identifiers, not even today's current names - this change also corrects that drift), the
  `pdata/migration-guidance` main spec (created by the just-archived migration-workflow change, names
  `pm-pdata-do-migrate` in several requirement bodies), source docstrings, and the bundled-skills discovery
  test - is updated to the new names.
- Historical records are deliberately left untouched: past `CHANGELOG.md` entries, archived
  `openspec/changes/archive/**`, and `docs/superpowers/plans/**`/`docs/superpowers/specs/**` planning
  docs describe what was true when written and are not rewritten to reflect a later rename.
- `pm-project-layout-reference` is explicitly out of scope for this change (left as-is, per Chris's
  instruction) - a separate rename of `pm-project-init` follows as its own change, stacked on this one.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `cli/bundled-pm-skills`: the requirement naming which skills this package bundles now names the
  current, correct skill identifiers (fixing pre-existing drift) and reflects the three renames above.

## Impact

- Skill directories: `src/cc_session_tools/skills/pm-pdata-do-migrate/` → `.../pm-pdata-do-manual-migration/`,
  `src/cc_session_tools/skills/pm-pdata-conflict-resolution/` → `.../pm-pdata-resolve-conflicts/`,
  `src/cc_session_tools/skills/pm-pdata-schema-design/` → `.../pm-pdata-design-schema/`.
- Cross-references: `src/cc_session_tools/skills/pm-pdata-do-audit-and-prepare-to-migrate/SKILL.md`,
  `src/cc_session_tools/skills/pm-project-init/SKILL.md`, `src/cc_session_tools/skills/
  pm-project-layout-reference/SKILL.md`, `src/cc_session_tools/lib/pdata/resolve.py`, `src/cc_session_tools/
  lib/pdata/service.py` (docstring mentions only - no behavior change).
- Tests: `tests/test_ccst_bundle_discovery.py` (literal name assertions).
- Specs: `openspec/specs/cli/bundled-pm-skills/spec.md`, `openspec/specs/pdata/migration-guidance/spec.md`.
- Per this repo's version policy this is a patch bump (3.3.0 → 3.3.1) per Chris's explicit instruction -
  a rename with no on-disk data format change and no CLI/config contract change, even though the skill
  identity itself is a breaking rename for anyone invoking the old slash command by name.
