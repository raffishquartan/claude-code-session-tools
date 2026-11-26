## ADDED Requirements

### Requirement: Interactive reviews are recorded through CCST
`ccst self-improvement record-review --mode <B|C> --findings <n> --applied <n> --deferred <n>` SHALL append exactly one line `<UTC ISO 8601 time with Z> mode=<B|C> findings=<n> applied=<n> deferred=<n>` to `reviews.log` in the state directory (creating the directory when missing, following `CCST_DATA_HOME`), print that line and exit 0. Every option SHALL be required; a mode other than `B` or `C`, or a count that is not a non-negative integer, SHALL be rejected with exit 2 and nothing written. The headless review and this command SHALL append lines through the same helper.

#### Scenario: Recording a periodic review
- **WHEN** `ccst self-improvement record-review --mode C --findings 3 --applied 1 --deferred 2` runs
- **THEN** `reviews.log` ends with a `mode=C findings=3 applied=1 deferred=2` line dated now, and days since the last review is 0

#### Scenario: Invalid count
- **WHEN** `--findings -1` or `--mode auto` is given
- **THEN** the command exits 2 and `reviews.log` is unchanged

## MODIFIED Requirements

### Requirement: A failing check escalates by Telegram, at most weekly
When the loop is set up and loop health or auto-review health is FAIL, the collector SHALL send one Telegram message through CCST's Telegram sender naming which check failed, the days since the last review and the signal total, and ending `Run /review-session-and-improve-claude-setup periodic`. It SHALL send none when the loop is not set up, when the `last-escalation` stamp is less than 7 days old, when `--no-escalate` is given or when `SELF_IMPROVEMENT_NO_ESCALATE=1` is set, and SHALL write that stamp only after a successful send. A failed send SHALL NOT make the collector fail.

#### Scenario: Health fails on the weekly run
- **WHEN** the loop is set up, loop health is FAIL and no escalation was sent in the previous 7 days
- **THEN** exactly one message is sent, naming the periodic review command, and a second run the same day sends none

#### Scenario: Telegram unavailable
- **WHEN** the send fails
- **THEN** the snapshot is written, no stamp is written, and the command exits 0

#### Scenario: Loop not set up
- **WHEN** no backlog exists and both health results are FAIL
- **THEN** the snapshot is written and no message is sent

### Requirement: A review is nudged at session start only when due
The SessionStart hook `self-improvement-nudge` SHALL never nudge until the loop is set up. Once it is, the hook SHALL decide that a review is due when no interactive review is recorded (a review dated in the future counts as none) or the last one is at least 30 days old; or the snapshot is valid and its total is at least 10; or the snapshot is valid, the last review is at least 14 days old and the total is at least 1. A snapshot SHALL be valid only when `SIGNAL_TOTAL` is an integer and every component is an integer or `unavailable`. When due, the hook SHALL emit JSON with the same line as `systemMessage` and as SessionStart `additionalContext`: `[ccst] Self-improvement review due: <last review N days ago|no review recorded>, <total signals (breakdown)|signals unknown (...)> - run /review-session-and-improve-claude-setup periodic`, and SHALL write today's local date to `last-nudge`. It SHALL emit nothing when not due or when `last-nudge` already holds today's local date, and SHALL always exit 0.

#### Scenario: Review due by age and signals
- **WHEN** the last review was 20 days ago and the snapshot shows 3 Inbox lines and a total of 3
- **THEN** one nudge line names 20 days, `3 signals`, `inbox 3` and `/review-session-and-improve-claude-setup periodic`

#### Scenario: Nothing due
- **WHEN** the last review was 5 days ago and the total is 4, or 14 days ago with a total of 0
- **THEN** the hook emits nothing

#### Scenario: Loop never used
- **WHEN** the backlog is missing or has no `## Inbox` heading, even though the state directory exists
- **THEN** the hook emits nothing and creates nothing

#### Scenario: Already nudged today
- **WHEN** a review is due and `last-nudge` holds today's date
- **THEN** the hook emits nothing

#### Scenario: Snapshot missing or malformed
- **WHEN** the snapshot is missing or malformed
- **THEN** the hook emits nothing if the last review is under 30 days old, and otherwise emits the nudge with `signals unknown` and no snapshot text

### Requirement: Nudge preview and status commands
`ccst self-improvement nudge` SHALL print the nudge line when a review is due, or `no review due`, ignoring and never writing `last-nudge`. `ccst self-improvement status` SHALL print whether the loop is set up (and, when it is not, that `ccst self-improvement init` enables it), that an interactive review is run with `/review-session-and-improve-claude-setup periodic`, the snapshot values, the last interactive review line, the last headless line and the headless cost since the last interactive review, and SHALL write nothing.

#### Scenario: Preview does not consume the daily nudge
- **WHEN** a review is due and `ccst self-improvement nudge` runs
- **THEN** it prints the nudge line and `last-nudge` is unchanged

#### Scenario: Status before setup
- **WHEN** no backlog exists and `ccst self-improvement status` runs
- **THEN** it prints that the loop is not set up, names `ccst self-improvement init` and `/review-session-and-improve-claude-setup periodic`, and creates nothing

### Requirement: The loop stays quiet until it is set up
The loop SHALL count as set up only when `~/.claude/self-improvement-backlog.md` (a symlink is followed) can be read and has a line exactly `## Inbox`. Until then the nudge hook SHALL emit nothing, `ccst self-improvement auto-review` SHALL make no model call, send no notification, log one `mode=auto status=skipped` line with cost 0 and print a summary naming `ccst self-improvement init`, and the collector SHALL send no escalation. None of them SHALL create the backlog. The summary lines of both commands SHALL be at most 1000 characters, so the scheduler's surfaced weekly digest shows them whole, including the `ccst self-improvement init` hint.

#### Scenario: CCST-only install, weekly jobs run
- **WHEN** no backlog exists and both jobs run
- **THEN** no model is called, no Telegram message is sent, the review log gains one `status=skipped` line with `cost_usd=0`, no backlog is created, and each job's one output line names `ccst self-improvement init`

#### Scenario: Backlog without an Inbox heading
- **WHEN** the backlog exists but has no `## Inbox` line
- **THEN** the run behaves as for a missing backlog

## REMOVED Requirements

### Requirement: The review command named in messages exists on the install
**Reason**: The `review-session-and-improve-claude-setup` skill is now bundled and installed with CCST, so the nudge and the escalation always name its periodic command.
**Migration**: None needed; `ccst skills install --apply` (part of every install sync) installs the skill.
