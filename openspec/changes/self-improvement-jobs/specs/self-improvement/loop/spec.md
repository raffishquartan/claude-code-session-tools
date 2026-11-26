## ADDED Requirements

### Requirement: The loop stays quiet until it is set up
The loop SHALL count as set up only when `~/.claude/self-improvement-backlog.md` (a symlink is followed) can be read and has a line exactly `## Inbox`. Until then the nudge hook SHALL emit nothing, `ccst self-improvement auto-review` SHALL make no model call, send no notification, log one `mode=auto status=skipped` line with cost 0 and print a summary naming `ccst self-improvement init`, and the collector SHALL send no escalation. None of them SHALL create the backlog.

#### Scenario: CCST-only install, weekly jobs run
- **WHEN** no backlog exists and both jobs run
- **THEN** no model is called, no Telegram message is sent, the review log gains one `status=skipped` line with `cost_usd=0`, and no backlog is created

#### Scenario: Backlog without an Inbox heading
- **WHEN** the backlog exists but has no `## Inbox` line
- **THEN** the run behaves as for a missing backlog

### Requirement: The review command named in messages exists on the install
The nudge and the escalation message SHALL name `/review-session-and-improve-claude-setup periodic` only when a skill with that name is installed under `~/.claude/skills/` (it has a `SKILL.md`), and SHALL name `ccst self-improvement status` otherwise.

#### Scenario: Review skill not installed
- **WHEN** a review is due and no `review-session-and-improve-claude-setup` skill is installed
- **THEN** the nudge ends `- run ccst self-improvement status` and does not mention the skill

## MODIFIED Requirements

### Requirement: The collector prints one summary line with both health results
The collector SHALL print exactly one line: `self-improvement: <total> signals (inbox <n>[/<oldest>d], entries <n>, msgs <n>, denies <n>, drift <n>, reliability <n>), last review <days>d|none, health PASS|FAIL, auto-review PASS|FAIL`. When the loop is not set up, the line SHALL end with `, loop not set up (run ccst self-improvement init)`.

#### Scenario: Summary line
- **WHEN** the collector runs with the last review 10 days ago and 16 signals
- **THEN** it prints one line containing `self-improvement: 16 signals`, `last review 10d`, `health PASS` and `auto-review PASS`

#### Scenario: Loop not set up
- **WHEN** the collector runs and no backlog exists
- **THEN** its one line ends with `loop not set up (run ccst self-improvement init)`

### Requirement: A failing check escalates by Telegram, at most weekly
When the loop is set up and loop health or auto-review health is FAIL, the collector SHALL send one Telegram message through CCST's Telegram sender naming which check failed, the days since the last review and the signal total, and pointing at the review command named in messages. It SHALL send none when the loop is not set up, when the `last-escalation` stamp is less than 7 days old, when `--no-escalate` is given or when `SELF_IMPROVEMENT_NO_ESCALATE=1` is set, and SHALL write that stamp only after a successful send. A failed send SHALL NOT make the collector fail.

#### Scenario: Health fails on the weekly run
- **WHEN** the loop is set up, loop health is FAIL and no escalation was sent in the previous 7 days
- **THEN** exactly one message is sent, and a second run the same day sends none

#### Scenario: Telegram unavailable
- **WHEN** the send fails
- **THEN** the snapshot is written, no stamp is written, and the command exits 0

#### Scenario: Loop not set up
- **WHEN** no backlog exists and both health results are FAIL
- **THEN** the snapshot is written and no message is sent

### Requirement: A review is nudged at session start only when due
The SessionStart hook `self-improvement-nudge` SHALL never nudge until the loop is set up. Once it is, the hook SHALL decide that a review is due when no interactive review is recorded (a review dated in the future counts as none) or the last one is at least 30 days old; or the snapshot is valid and its total is at least 10; or the snapshot is valid, the last review is at least 14 days old and the total is at least 1. A snapshot SHALL be valid only when `SIGNAL_TOTAL` is an integer and every component is an integer or `unavailable`. When due, the hook SHALL emit JSON with the same line as `systemMessage` and as SessionStart `additionalContext`: `[ccst] Self-improvement review due: <last review N days ago|no review recorded>, <total signals (breakdown)|signals unknown (...)> - run <review command named in messages>`, and SHALL write today's local date to `last-nudge`. It SHALL emit nothing when not due or when `last-nudge` already holds today's local date, and SHALL always exit 0.

#### Scenario: Review due by age and signals
- **WHEN** the last review was 20 days ago, the review skill is installed and the snapshot shows 3 Inbox lines and a total of 3
- **THEN** one nudge line names 20 days, `3 signals`, `inbox 3` and the periodic-review command

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
`ccst self-improvement nudge` SHALL print the nudge line when a review is due, or `no review due`, ignoring and never writing `last-nudge`. `ccst self-improvement status` SHALL print whether the loop is set up (and, when it is not, that `ccst self-improvement init` enables it), the snapshot values, the last interactive review line, the last headless line and the headless cost since the last interactive review, and SHALL write nothing.

#### Scenario: Preview does not consume the daily nudge
- **WHEN** a review is due and `ccst self-improvement nudge` runs
- **THEN** it prints the nudge line and `last-nudge` is unchanged

#### Scenario: Status before setup
- **WHEN** no backlog exists and `ccst self-improvement status` runs
- **THEN** it prints that the loop is not set up and names `ccst self-improvement init`, and creates nothing

### Requirement: The headless model call uses only the subscription login
The model call SHALL be `claude -p --model sonnet --max-budget-usd 0.50 --output-format json --no-session-persistence --restricted --strict-mcp-config --tools ""` with the prompt and digest on stdin, run from the temporary directory, with every variable of CCST's single subscription strip list removed from its environment: API-billed credentials (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and the gateway and third-party provider keys and tokens), third-party provider switches (`CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY` and the others the `claude` CLI honours), and API routing variables (`ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS` and the provider base URLs). `CLAUDE_CODE_OAUTH_TOKEN` SHALL be kept. Before it, the job SHALL check `claude auth status --json` under the same environment and SHALL NOT call the model unless it reports a logged-in first-party subscription login (`authMethod` `claude.ai` or `oauth_token`). A missing login or a missing `claude` command SHALL be logged as `failed` with cost 0, notified, and SHALL NOT block the next run. The job SHALL NOT fall back to any other credential. The model call SHALL be stopped after `SELF_IMPROVEMENT_MODEL_TIMEOUT_S` seconds (default 240) or when the job's own budget `SELF_IMPROVEMENT_JOB_BUDGET_S` (default 270) runs out, with a forced kill 3 seconds after a termination request that is ignored; a run with no time left SHALL NOT start the model and SHALL be logged `failed` with cost 0.

#### Scenario: API key in the environment
- **WHEN** `ANTHROPIC_API_KEY` and `CLAUDE_CODE_USE_BEDROCK` are set in the job's environment
- **THEN** neither is present in the environment of the `claude` processes

#### Scenario: Gateway in the environment
- **WHEN** `ANTHROPIC_BASE_URL` and `ANTHROPIC_CUSTOM_HEADERS` are set in the job's environment
- **THEN** neither is present in the environment of the `claude` processes, so the digest goes only to the first-party API

#### Scenario: No subscription login
- **WHEN** `claude auth status` reports not logged in, or `authMethod` `api_key`
- **THEN** no model call is made, a `failed` line with cost 0 is logged and one Telegram message is sent

#### Scenario: Model call ignores termination
- **WHEN** the model call ignores SIGTERM at its time limit
- **THEN** it is killed, and a `failed` line with cost `unknown` is logged and notified

### Requirement: Headless output is validated and only appended to the Inbox
The job SHALL keep only lines of the exact form `- [yyyy-MM-dd] [auto-review] <lever>: <friction> -> <proposal>` whose lever is one of `claude-md`, `skill`, `hook`, `permission`, `settings`, `mcp-plugin`, at most 10, and SHALL drop a line whose text after the date matches an `[auto-review]` line already in the backlog or earlier in the same output. It SHALL insert the kept lines after the last non-blank line of the `## Inbox` section of the backlog (resolving a symlink) with an atomic replace, leaving every other byte unchanged. When the loop is not set up before the run, the job SHALL behave as the requirement "The loop stays quiet until it is set up" says. When the `## Inbox` heading has gone by the time the output is appended, or the write fails, the backlog SHALL be left unchanged and the run logged `failed`.

#### Scenario: Model output contains invalid lines
- **WHEN** the output contains prose, an unknown lever, a `none` lever, a line without ` -> `, an indented line, a duplicate and more than 10 valid lines
- **THEN** exactly 10 new valid lines are appended inside the Inbox and the rest of the backlog is byte-identical

#### Scenario: Same proposal repeated in a later week
- **WHEN** the model repeats an `[auto-review]` line already in the backlog with an earlier date
- **THEN** that line is not appended

### Requirement: Every headless attempt is logged with its cost and escalated on failure or overspend
Every attempt SHALL append one `mode=auto` line to the review log. The cost SHALL be `total_cost_usd` from the JSON result when it parses, `0` when the model was never started, and `unknown` otherwise. Status SHALL be `over-budget` when the cost exceeds 0.50 (even if the result reports an error), `failed` when the call exits non-zero, times out, reports `is_error`, returns unparseable output, or the Inbox write fails, and `ran` otherwise. `last-auto-review` SHALL be set to the run's start time on `ran` and `over-budget`. A `failed` or `over-budget` run SHALL send one Telegram message `Self-improvement auto-review <status> (cost USD <cost>)`, with `: <reason>` after the status when the run stopped before the model call (`no time left`, `claude not found`, `no subscription login`); a failed send SHALL NOT change the logged status. The command SHALL print one summary line and exit 0.

#### Scenario: Successful run
- **WHEN** the run completes at cost 0.12 with 10 valid proposals from 1 session
- **THEN** the line is `... mode=auto status=ran sessions=1 proposals=10 cost_usd=0.12 duration_s=<n>`, `last-auto-review` exists and nothing is sent

#### Scenario: Run exceeds budget
- **WHEN** the reported cost is 0.60
- **THEN** an `over-budget` line is logged and one message is sent
