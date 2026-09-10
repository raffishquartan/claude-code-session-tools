## Why

`ccst skills install` / `install-everything` / `ensure_synced` is additive-only: it creates a
symlink under `~/.claude/skills/` for every currently-bundled skill, but never removes one for a
name that stopped being bundled (a rename or removal). `ccst doctor`'s
`check_no_stale_skill_symlinks` (added in commit `b819116`) already detects the resulting dangling
symlinks and WARNs, but only ever suggests a manual `rm`. Every `pm-*` skill rename since then
(`fa68c49`, `68fb702`, `80bf322`) has left exactly this kind of orphaned symlink behind on any
machine that had the old name installed, with no automatic recovery. Hooks already self-heal the
structurally identical problem via `prune_stale_hooks` (`hooks_install.py:84`); skills have no
equivalent.

## What Changes

- Add a `prune_stale_skill_symlinks()` helper, mirroring `prune_stale_hooks`, that removes a
  symlink under the target skills directory when it is a dangling symlink pointing into this
  package's skills source tree (i.e. one this tool manages) and no longer matches any
  currently-bundled skill name.
- Wire it into `_cmd_skills_install()` so `ccst skills install` (and therefore
  `install-everything` and the `ensure_synced` auto-sync path) prunes stale symlinks in the same
  pass that installs current ones — a rename now self-heals on the next sync, the same way a
  hook rename already does.
- Only ever remove a symlink this tool manages (dangling, and its stored target lives under this
  package's skills source directory) — never touch a non-symlink or a symlink pointing somewhere
  else, so a user's own unrelated symlink under `~/.claude/skills/` is untouched.
- No change to `check_no_stale_skill_symlinks`'s detection logic itself; it remains the doctor-side
  safety net for whatever a sync pass didn't reach (e.g. `ensure_synced` skipped because the
  invoking command is exempt).

## Capabilities

### New Capabilities
- `cli/skills-stale-symlink-prune`: `ccst skills install` (and its callers `install-everything` /
  `ensure_synced`) automatically removes a dangling, tool-managed skill symlink left behind by a
  rename or removal, instead of leaving it for manual cleanup.

### Modified Capabilities
(none — `cli/skills-stale-symlink-check`'s existing WARN behavior is unchanged; it still fires for
whatever the new pruning step doesn't reach)

## Impact

- `src/cc_session_tools/cli/ccst.py`: `_cmd_skills_install()` gains a prune step.
- New helper, likely alongside `_discover_skills()` / `_decide_action()` in `ccst.py`, or in a
  shared module if `hooks_install.py`'s `prune_stale_hooks` is a reasonable model to colocate
  with (implementation detail for design.md).
- No CLI flag or output-format change; `ccst skills install`'s existing dry-run/`--apply` behavior
  extends to cover removals the same way it already covers creates.
- Patch version bump (bug fix, no interface change) once implemented.
