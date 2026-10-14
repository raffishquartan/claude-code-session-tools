## Why

`ccst claude-md install` (and the automatic post-upgrade sync that runs it) replaces a managed `CCST:<section>` block with the current template whenever the two differ. It cannot tell a stale template from a hand edit, so a customised block is overwritten silently and unrecoverably. This happened to the `workflow` section on the 3.15.0 upgrade. Nothing warns beforehand, nothing keeps the lost text, and `ccst doctor` has no view of it.

## What Changes

- Record, per CLAUDE.md file and managed section, a hash of the block text last written by ccst.
- Before replacing an existing block, compare it with the recorded hash. If the block does not match what ccst last wrote (a hand edit, or no record yet), save its text as a backup first.
- Report every such overwrite: the claude-md step output and the auto-sync stderr line name the section and the backup id.
- Add a `ccst doctor` check that WARNs while a managed block differs from both the current template and the last-written hash, so a pending hand edit is visible before the next upgrade overwrites it.
- Add `ccst claude-md backups list` and `ccst claude-md backups show <id>` so a backed-up block can be read and merged back by hand.
- Minor release (3.16.0): additive store and subcommands, nothing existing becomes unreadable.

## Capabilities

### New Capabilities

### Modified Capabilities
- `install/claude-md-fragment`: adds requirements for hand-edit detection, backup before overwrite, overwrite reporting, the doctor check, and the backups query subcommands.

## Impact

- `src/cc_session_tools/lib/claude_md_install.py` (detection, backup, results).
- `src/cc_session_tools/lib/sessions_db.py` (new `claude_md_blocks` and `claude_md_backups` tables, following the shared connection helper).
- `src/cc_session_tools/cli/ccst.py` (output, `claude-md backups` subcommands) and `src/cc_session_tools/lib/doctor.py` (new check).
- Tests for each requirement; `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (3.16.0).
- Stacked on PR 173 (3.15.1); this proposal assumes that branch's version and spec.
