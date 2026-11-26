## Why

The previous change ported the self-improvement loop into CCST but left both scheduled jobs unregistered, so the loop only runs when the user schedules it by hand (or keeps a config repository's bash versions registered). Bundling the jobs makes the loop run on every CCST install, which first needs a CCST-only install to stay quiet until the user opts in: today a missing backlog makes the weekly review log `failed` and notify every week, the nudge names a skill the install may not have, and a gateway set in `ANTHROPIC_BASE_URL` would receive the digest.

## What Changes

- Bundle two ccsched jobs in CCST's default set, enabled by default:
  - `self-improvement-signals`: `every:7d`, coalesce `one`, catch-up window `14d`, timeout `60s`, surfaced, runs `ccst self-improvement collect-signals` (counts only, no model call).
  - `self-improvement-auto-review`: `every:7d`, coalesce `one`, catch-up window `14d`, timeout `300s`, surfaced, runs `ccst self-improvement auto-review` (subscription login only, at most one paying run per 7 days, USD 0.50 cap, proposal-only).
- The ids match the ones a config repository registered for its bash scripts. `ccst ccsched-jobs install` reports such a registration as a conflicting definition with the migration steps, never replaces it, and does not record it as a CCST install (so removing it and re-running the installer registers CCST's version).
- New "loop set up" rule: the backlog `~/.claude/self-improvement-backlog.md` exists and has a `## Inbox` heading. Until then the nudge is silent, `auto-review` logs `skipped` with cost 0 and sends nothing (no model call), and the collector still writes its snapshot but sends no escalation and says how to enable the loop.
- New `ccst self-improvement init`: creates the backlog with `## Inbox` and `## Entries` headings; idempotent, never overwrites. `status` reports whether the loop is set up and how to enable it.
- The nudge and the escalation message name `/review-session-and-improve-claude-setup periodic` only when that skill is installed; otherwise they name `ccst self-improvement status`.
- The subscription-only environment also removes `ANTHROPIC_BASE_URL` and the other routing, gateway and third-party provider credential variables the `claude` CLI honours, from the one shared list in `lib/api_keys.py`.
- `ccst doctor`: the bundled-job check reports a conflicting bash-script registration with the migration steps (WARN), and a new `self-improvement:loop` check WARNs when either job is registered but the loop is not set up.

## Capabilities

### New Capabilities
- `self-improvement/scheduled-jobs`: the two bundled ccsched jobs, how the installer and `ccst doctor` treat an existing registration of the same ids, the loop-setup doctor check and `ccst self-improvement init`.

### Modified Capabilities
- `self-improvement/loop`: loop-set-up gating of the nudge, the headless review and the escalation; the review-command wording; the collector summary line; the wider stripped environment; `status` output.

## Impact

- Code: `lib/scheduler/bundled_jobs.py`, `cli/ccst.py` (`ccsched-jobs install`, `self-improvement init`, `status`), `lib/self_improvement/{state,nudge,signals,review,report}.py`, `lib/api_keys.py`, `lib/doctor.py`, `src/hooks/self_improvement_nudge.py` (docstring).
- Every install gets two more weekly jobs on its next sync. The auto-review job can spend subscription usage (at most USD 0.50 per 7 days) once the user runs `ccst self-improvement init`; before that it makes no model call.
- An install that already registered the bash versions under the same ids keeps them until the user migrates (`ccsched remove <id>`, then `ccst ccsched-jobs install --apply`).
- Version 4.11.0 (minor: new jobs, new subcommand, new doctor check).
