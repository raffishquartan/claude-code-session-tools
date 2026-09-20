## Why

`claude-code-usage reconcile`, and `ccusage blocks` (the authoritative per-5-hour-block view needed
when diagnosing an exhausted usage allowance), both need the upstream `ccusage` CLI. The
`analyse-cc-usage` skill's "Setup on a new machine" step 4 says "Install bun first if needed" and
then `bun add -g ccusage`, but it is easy to skip and nothing ever notices: on one machine `bun`,
`bunx` and `ccusage` were all absent, `claude-code-usage reconcile` could not run, and the gap only
surfaced mid-investigation. Nothing on an existing machine that upgrades CCST re-checks for it
either, because `ccst doctor` has no check for these tools.

Reported via `ccmsg` `20260919T231009Z-21a9`.

## What Changes

- `analyse-cc-usage/SKILL.md` step 4 becomes explicit: a separate step to install `bun` (with the
  concrete upstream install command, per platform), verify with `bun --version`, ensure bun's global
  bin directory is on `PATH` for non-interactive shells, then `bun add -g ccusage` and verify with
  `ccusage --version`.
- New `ccst doctor` checks `deps:bun` and `deps:ccusage`. Both WARN (not FAIL - `ccusage` is an
  optional dependency, `claude-code-usage` works without it apart from `reconcile`) when the tool is
  not resolvable, and the reason carries the exact install command. A third state - `ccusage`
  installed under bun's global bin directory but not on `PATH` - WARNs with the `PATH` fix instead of
  the install command. Doctor already runs as the last step of `ccst install-everything` (and so on
  every upgrade that triggers a sync via `ccst`), which is what makes the gap visible on an existing
  machine after an update, not only on a new one.
- `claude-code-usage`'s ccusage lookup resolves `ccusage` via `PATH` first, then bun's global bin
  directory (`$BUN_INSTALL/bin`, default `~/.bun/bin`), so a correct install is found from
  non-interactive shells (hooks, scheduled jobs, subagent Bash calls) where the interactive-shell
  `PATH` edit never applies.
- The `CcusageNotInstalled` error message points at the skill's setup step and `ccst doctor`.

## Capabilities

### New Capabilities
- `cli/ccusage-dependency`: how CCST detects, reports and locates the optional `bun` / `ccusage`
  dependency (doctor checks, lookup order, install instructions).

### Modified Capabilities
(none)

## Non-goals

- CCST does not install `bun` or `ccusage` itself. See design.md for why (auto-sync runs unattended,
  a network install from there is a side effect users did not ask for). This is the main decision
  to review.

## Impact

- `src/cc_session_tools/skills/analyse-cc-usage/SKILL.md`: step 4 rewritten.
- `src/cc_session_tools/lib/doctor.py` and the `ccst doctor` wiring in
  `src/cc_session_tools/cli/ccst.py`: two new checks (a third state inside `deps:ccusage`).
- `src/claude_code_usage/ccusage_wrapper.py`: lookup order, error message.
- Tests for each. README's "`ccusage` (optional)" line updated.
- CHANGELOG entry under `[Unreleased]`. Version bump (minor) is deferred to the stacked follow-up change, which carries one bump for both.
