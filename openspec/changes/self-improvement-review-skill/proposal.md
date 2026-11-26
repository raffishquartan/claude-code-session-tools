## Why

CCST ships the automated half of the self-improvement loop (signal collector, headless review, nudge, bundled weekly jobs) but not the interactive review that turns proposals into approved changes and writes the `mode=B`/`mode=C` lines the loop health depends on. Without it, a CCST-only install that runs `ccst self-improvement init` sees loop health FAIL forever and a nudge that repeats daily, and every message has to fall back to `ccst self-improvement status`. Bundling the review skill closes the loop on every install.

## What Changes

- Bundle the `review-session-and-improve-claude-setup` skill in `src/cc_session_tools/skills/`, installed, synced, uninstalled and checked by `ccst doctor` like every other bundled skill. Its Mode A (inline Inbox note), Mode B (full session review) and Mode C (periodic review) and the review log behaviour are kept; its bash and `awk` steps become `ccst self-improvement ...` commands, and it carries no personal or machine-specific details.
- New `ccst self-improvement record-review --mode B|C --findings N --applied N --deferred N`: appends one interactive line to `reviews.log` in the state directory CCST reads (it follows `CCST_DATA_HOME`), so the skill never hand-formats a line or hardcodes the path.
- The nudge and the FAIL escalation always name `/review-session-and-improve-claude-setup periodic`; the "skill not installed" fallback to `ccst self-improvement status` is removed. `status` always says to run that command.
- The weekly digest lines of both jobs, while the loop is not set up, keep naming `ccst self-improvement init` (already true; now pinned by tests and spec scenarios).
- `ccst doctor`'s `self-improvement:loop` WARN is unchanged.
- README, CLI help and specs describe the skill as the interactive part of the loop.

## Capabilities

### New Capabilities
- `skills/review-session-and-improve-claude-setup`: the bundled interactive review skill - its modes, its checkpoint before any change, and how it records a review through CCST.

### Modified Capabilities
- `self-improvement/loop`: the review command named in messages is always the bundled skill's periodic review; `status` names it; new `record-review` command; the not-set-up summary lines name `init`.

## Impact

- Code: new `src/cc_session_tools/skills/review-session-and-improve-claude-setup/SKILL.md`; `lib/self_improvement/{signals,nudge,report,state,review}.py`; `cli/ccst.py` (`record-review`).
- Every install gets one more skill symlink on its next sync. **An install that already has a real (non-symlink) `~/.claude/skills/review-session-and-improve-claude-setup` directory** (for example one copied there by a config repository) makes `ccst skills install --apply`, and so the automatic install sync, exit 1 until that directory is removed; `ccst doctor` then reports `skill:review-session-and-improve-claude-setup` and `install:synced` as FAIL. The README and CHANGELOG give the one-off step.
- Version 4.12.0 (minor: new bundled skill, new subcommand).
