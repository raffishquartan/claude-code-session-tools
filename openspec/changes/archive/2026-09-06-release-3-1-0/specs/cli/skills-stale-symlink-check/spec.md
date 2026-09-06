## Purpose

Flags a `~/.claude/skills/` symlink left behind after a bundled skill is renamed or removed, so
an upgrade doesn't leave a broken symlink sitting unnoticed.

## ADDED Requirements

### Requirement: `ccst doctor` flags a skill symlink with no corresponding bundled skill
`ccst doctor` SHALL report a symlink under the installed skills directory whose target no longer
exists (e.g. because the bundled skill it pointed to was renamed or removed).

#### Scenario: A renamed skill leaves its old symlink flagged
- **WHEN** a bundled skill is renamed and the currently-installed skill set still has a symlink
  under the old name pointing at a now-nonexistent directory
- **THEN** `ccst doctor` reports that symlink, naming it and suggesting it be removed

#### Scenario: A fully up-to-date install has nothing to flag
- **WHEN** every symlink under the installed skills directory resolves to an existing bundled
  skill
- **THEN** `ccst doctor` reports no stale skill symlinks
