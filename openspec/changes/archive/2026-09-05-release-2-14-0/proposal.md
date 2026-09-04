## Why

Three independent pdata maintenance gaps and one skill-packaging gap are due: `ccst pdata init`
gives a genuinely new project no starting folder structure at all; the migration manifest file's
name reads as a scratch/draft artifact when it is actually permanent tool state, with no fallback
if a project renames or loses track of it; `ccst pdata verify` silently reports OK for a project
that was migrated and then lost its manifest, indistinguishable from a project that was never
migrated at all; and two already-built, portable `pm-*` skills exist only on one machine
(`~/.claude/skills/`), invisible to `ccst skills install`/`install-everything` and to any other
machine.

## What Changes

- `ccst pdata init` scaffolds `correspondence/`, `meetings-and-calls/`, and `workstreams/` for a
  genuinely new project (root directory did not exist before this call) - not on every dry-run or
  re-run of an existing project. Framed as optional starting structure, not mandatory: research
  into `pm-project-layout-reference`'s own guidance ("not every project needs all five... decide
  which apply") and a survey of 9 real `~/cc/*` projects confirms most projects use only a subset
  of these folders or none at all (two already-migrated projects use a plain `data/` folder
  instead). Scaffolding fires once, for new projects only, so it costs nothing for the majority of
  existing/differently-structured projects and gives new ones a visible starting point they can
  freely delete what they don't need.
- The `pdata` migration-manifest file is renamed from `.ccst-pdata-proposal.json` to
  `.pdata-migration-manifest.json`, reflecting that it is permanent tool state, not a draft -
  documented as such in its own CLI help text and `--write` success message. A single resolver
  function reads the new name if present, else falls back to the legacy name, so existing
  already-migrated projects keep working without a forced rename.
- `ccst pdata verify` cross-checks three migration-state signals independently (manifest
  presence, `.pdata-migrated/<record_group>/` archive presence, and the project's `.db` row
  counts) instead of skipping its comparison entirely whenever the manifest file is absent. A
  project with migration evidence (archived files and/or populated tables) but no manifest is now
  flagged with a distinct issue and recovery pointers (`ccst pdata schema show`/`schema list`),
  rather than silently reporting OK - the exact "migrated, manifest now missing" gap a real
  project (`home`) hit.
- `pm-pdata-audit` and `pm-pdata-migrate` (built directly under `~/.claude/skills/` in an earlier
  session; already portable, no project-specific content) are moved into this repo's bundled
  `src/cc_session_tools/skills/` directory alongside the rest of the `pm-*` family, so
  `ccst skills install`/`install-everything` provision them like every other bundled skill - no
  registry list to update, this repo's skill discovery is a directory scan. **BREAKING for
  neither** - this only adds two skills to what gets installed; existing installs of either skill
  under `~/.claude/skills/` are left as plain directories until the next `ccst skills install`
  replaces them with the managed symlink, matching how every other bundled skill already behaves
  on first sync.

## Capabilities

### New Capabilities
- `pdata/init-scaffolding`: what folder structure a new project gets from `ccst pdata init`, and
  under what conditions.
- `pdata/manifest-naming`: the migration-manifest filename, its documented permanence, and
  backward-compatible resolution of the legacy name.
- `pdata/verify-integrity`: `ccst pdata verify`'s three-signal cross-check for distinguishing
  never-migrated from migrated-but-manifest-missing projects.
- `cli/bundled-pm-skills`: which `pm-*` skills this repo bundles and installs via the standard
  skill-sync mechanism.

### Modified Capabilities
(none - no existing main spec covers pdata init, the manifest filename, verify, or which skills
are bundled)

## Impact

- `src/cc_session_tools/lib/pdata/init_paths.py` (`PROPOSAL_FILENAME` rename + new
  backward-compatible resolver; new folder-scaffolding list/logic)
- `src/cc_session_tools/lib/pdata/init_service.py` (`resolve_project_root()`'s dry-run/write
  call sites; scaffolding hook point gated on "root did not exist before this call")
- `src/cc_session_tools/lib/pdata/verify.py` (`check_row_count_parity()`'s manifest-missing
  early return; new independent checks for `.pdata-migrated/` presence and `.db` row-group
  presence)
- `src/cc_session_tools/lib/pdata/manifest.py`, `rename_group.py`, `reorganize.py`, `classify.py`
  (all already import `PROPOSAL_FILENAME` - pick up the rename for free; `rename_group.py:156`
  interpolates it into an error message)
- `src/cc_session_tools/cli/ccst.py:2975` (the one literal-string duplicate of the manifest
  filename, in `rename-group --write`'s argparse help text; `--write` success message already
  prints the resolved `Path` object, no change needed there)
- `src/cc_session_tools/skills/pm-project-init/SKILL.md` (prose references to the manifest
  filename)
- `src/cc_session_tools/skills/pm-pdata-audit/`, `src/cc_session_tools/skills/pm-pdata-migrate/`
  (new bundled skill directories, copied from `~/.claude/skills/`)
- `TODO.md` - no entries resolved by this change (items #3-5, #7, #16 come from
  `cc-sessions/20260904-update-ccst/working/ccst-task-list.md`, not TODO.md)
