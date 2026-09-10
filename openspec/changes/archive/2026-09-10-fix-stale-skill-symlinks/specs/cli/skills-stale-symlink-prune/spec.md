## Purpose

Removes a `~/.claude/skills/` symlink that this tool created for a skill that has since been
renamed or removed from the bundle, so an upgrade self-heals the orphan instead of leaving it for
a human to `rm` by hand.

## ADDED Requirements

### Requirement: `ccst skills install` removes a tool-managed symlink whose skill no longer exists
`ccst skills install` (and therefore `install-everything` and the `ensure_synced` auto-sync path)
SHALL remove a symlink under the target skills directory when both are true: the symlink is
dangling (its target does not exist), and its stored target path lies under this package's
bundled skills source directory. This applies whether `--apply` is used directly or reached via
`install-everything --apply` / `ensure_synced`'s automatic sync.

#### Scenario: A renamed skill's old symlink is removed on the next install
- **WHEN** a bundled skill is renamed, the currently-installed skill set still has a symlink under
  the old name pointing at a now-nonexistent directory under this package's skills source, and
  `ccst skills install --apply` runs
- **THEN** the old symlink is removed and the new name is installed, leaving no orphan behind

#### Scenario: A dry run reports the removal without making it
- **WHEN** `ccst skills install` runs without `--apply` and finds a tool-managed dangling symlink
- **THEN** it reports the symlink as one it would remove, and does not remove it

#### Scenario: A user's unrelated symlink is left alone
- **WHEN** an entry under the target skills directory is a symlink whose target does not lie under
  this package's bundled skills source directory (i.e. it is not one this tool created)
- **THEN** `ccst skills install` does not remove or otherwise modify it, even if that symlink is
  also dangling

#### Scenario: A non-symlink entry is left alone
- **WHEN** an entry under the target skills directory with a name matching no currently-bundled
  skill is a real directory or file, not a symlink
- **THEN** `ccst skills install` does not remove it
