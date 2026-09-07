## Context

See proposal.md - Why. Second of two stacked renames: branches from the tip of
`f/20260907-rename-pdata-skills-batch1` (PR #141, itself stacked on the still-open PR #140), not
`main`. Same mechanics as that change: `_discover_skills` (`src/cc_session_tools/cli/ccst.py`) is
a pure directory scan, so renaming the directory is the entire "bundling" change; everything else
is prose/test references catching up.

`pm-project-init` is the most cross-referenced of the four `pm-pdata-*`-family skills - it's the
skill every other pdata-migration skill points a reader toward as the recommended path, per PR
#140's own deprecation work - which is why Chris asked for this rename kept separate and later in
the stack rather than bundled into batch 1.

## Goals / Non-Goals

**Goals:**
- Rename `pm-project-init` → `pm-pdata-do-init` and every currently-live reference to it.

**Non-Goals:**
- No compatibility alias for `/pm-project-init` - matches `rename-pdata-skills-batch1`'s own
  precedent.
- No edits to historical record (past `CHANGELOG.md` entries, `openspec/changes/archive/**`,
  `docs/superpowers/**`) - same rule as the prior change.
- `pm-project-layout-reference` stays as-is (cites `pm-project-init` in passing, but the file
  itself is out of scope, per Chris's instruction).

## Decisions

### Same mechanics as `rename-pdata-skills-batch1`: `git mv`, then grep-scoped reference updates
No new pattern needed - `git mv` for the directory (preserves history), then update every
currently-live cross-reference found by `grep -rl "pm-project-init"`, excluding
`openspec/changes/archive/**`, `docs/superpowers/**`, and past `CHANGELOG.md` entries.

### Both live main specs get updated in the sync step, not hand-edited during apply
`cli/bundled-pm-skills` (one scenario names `pm-project-init` in passing - handled via this
change's own delta spec) and `pdata/migration-guidance` (created by PR #140 - re-grepped before
writing this design, and it turns out to name `pm-project-init` extensively: six of its
requirements are literally headed `### Requirement: \`pm-project-init\` documents/sets...`, plus
several scenario bodies). Both get updated the same way `rename-pdata-skills-batch1` handled its
own out-of-band `pdata/migration-guidance` fix: a plain text substitution across the whole file
during the archive/sync step, including the requirement header names themselves (their identity
is tied to the skill they document, same reasoning as `rename-pdata-skills-batch1`'s
`cli/bundled-pm-skills` requirement-header rename).

## Risks / Trade-offs

- [Risk] `/pm-project-init` muscle memory breaks after this ships and a reinstall runs. →
  Mitigation: Chris's own deliberate choice, consistent with the prior rename; CHANGELOG entry
  names the mapping.
