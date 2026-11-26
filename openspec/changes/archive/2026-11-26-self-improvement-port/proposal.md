## Why

The Claude configuration self-improvement loop (a weekly signal collector, a cost-capped weekly headless review, and a SessionStart nudge) exists today only as bash scripts in a separate config repository. A default scheduled job that points at those scripts would fail on every CCST install that lacks that repository, so the loop has to ship inside CCST before CCST can register its jobs. This change ports the functionality; registering the scheduled jobs is a separate follow-up change.

## What Changes

- New `ccst self-improvement` command with four subcommands:
  - `collect-signals`: counts-only snapshot of improvement signals, loop-health and auto-review-health PASS/FAIL, one summary line, rate-limited Telegram escalation on FAIL. No model call.
  - `auto-review [--force]`: at most one paying headless review per 7 days, using the Claude Code subscription login only (API-key and third-party provider variables are removed from the child environment, and the run fails with a logged status when no subscription login is available). USD 0.50 hard cap per run, no tools, proposal-only: appends validated lines to the backlog Inbox and never edits configuration.
  - `nudge`: prints whether a review is due and the nudge line, without recording a nudge.
  - `status`: prints the current snapshot, last interactive review, last headless run and both health results.
- New SessionStart hook `ccst hooks run self-improvement-nudge`, added to the default hook bundle, that shows the nudge at most once a day when a review is due, and stays silent when the config repository's own bash nudge is installed in the same settings file.
- State stays in `~/.local/share/claude/self-improvement/` with the same file names and formats (`signals.env`, `reviews.log`, `drift-push-count`, `last-nudge`, `last-escalation`, `last-auto-review`), so existing installs carry over and both implementations can run against the same state.
- Notifications go through CCST's existing Telegram sender (`lib.scheduler.notify.send_telegram`); no new credential handling.
- README section for the new command, the hook, the state directory and the subscription-usage cost.

## Capabilities

### New Capabilities
- `self-improvement/loop`: signal collection, loop and auto-review health, escalation, the session-start nudge, the bounded headless review and its run log, ported from the config repository's `self-improvement-loop` spec and adapted to CCST (subscription-only auth, CCST notifier, CCST hook and subcommands, file contract for the drift count and review log written by other tools).

### Modified Capabilities
<!-- none -->

## Impact

- New package `src/cc_session_tools/lib/self_improvement/` and new hook module `src/hooks/self_improvement_nudge.py`.
- `src/cc_session_tools/cli/ccst.py`: new `self-improvement` noun.
- `src/cc_session_tools/lib/hook_registry.py` and `config/hooks-bundle.json`: new hook.
- New bundled data file `config/self-improvement-review-prompt.md`.
- Reads (never writes) `ccmsg.db` and `telemetry.db` through the CCST Python APIs; reads `~/.claude/projects/**/*.jsonl`, `~/.claude/usage-data/facets/`, `~/.claude/skills/*/references/*.md` and `~/.claude/self-improvement-backlog.md`; appends to the backlog Inbox only.
- Spends subscription usage: at most one `claude -p` call a week, capped at USD 0.50, once the follow-up change registers the job. Nothing in this change schedules it.
- Minor version bump (4.10.0): additive.
