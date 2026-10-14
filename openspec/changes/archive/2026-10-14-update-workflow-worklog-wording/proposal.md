## Why

The managed `workflow` section of `~/.claude/CLAUDE.md` only asks sessions to "keep `working/WORKLOG.md` updated". A stronger paragraph was hand-edited into a synced CLAUDE.md, but the ccst 3.15.0 upgrade re-rendered the block from the template and reverted it. Until the template carries the stronger wording, every upgrade reverts it again.

## What Changes

- Replace the WORKLOG paragraph in the `workflow` section template with wording that makes the WORKLOG mandatory for every `ccd`/`ccr` session, created as the first action, appended to incrementally, with one `yyyy-MM-dd HH:mm`-headed entry per meaningful step or decision.
- The `worklog-guard` sentence is kept.
- No other section text changes. Patch release (3.15.1): wording only, no interface change.
- Detecting hand-edits that an upgrade would overwrite is out of scope here; it is a separate follow-up proposal.

## Capabilities

### New Capabilities

### Modified Capabilities
- `install/claude-md-fragment`: the `workflow` section requirement now states that a WORKLOG is mandatory, created first, and appended incrementally.

## Impact

- `src/cc_session_tools/lib/claude_md_install.py` (the `workflow` entry in `_SECTION_BODIES`).
- `tests/test_claude_md_install.py` (assert the new wording).
- `CHANGELOG.md`, `pyproject.toml` and `uv.lock` (3.15.1).
- Installed `~/.claude/CLAUDE.md` files pick the change up through the normal post-upgrade sync.
