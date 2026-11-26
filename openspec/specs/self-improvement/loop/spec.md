# self-improvement/loop Specification

## Purpose
Keeps the Claude configuration self-improvement loop running without anyone having to remember it: cheap deterministic signals are counted, a cost-capped headless review proposes findings into a backlog, a review is nudged at session start when the signals warrant one, and loop health is measured, while every configuration change still passes a human checkpoint.

## Requirements

### Requirement: Signals are collected deterministically into a counts-only snapshot
`ccst self-improvement collect-signals` SHALL make no language-model call. It SHALL replace `signals.env` in `~/.local/share/claude/self-improvement/` atomically with `KEY=value` lines, in this order: `COLLECTED_AT` (UTC ISO 8601 with `Z`), `INBOX_COUNT`, `INBOX_OLDEST_DAYS`, `BACKLOG_OPEN`, `MSGS_UNCLAIMED_14D`, `DENIES_7D`, `DRIFT_PUSH`, `RELIABILITY_OPEN`, `SIGNAL_TOTAL`, `DAYS_SINCE_REVIEW`, `LOOP_HEALTH`, `AUTO_REVIEW_HEALTH`. The counts SHALL be:
- `INBOX_COUNT`: dated bullets `- [yyyy-MM-dd] ...` with a valid calendar date in the `## Inbox` section of `~/.claude/self-improvement-backlog.md`; `INBOX_OLDEST_DAYS` the age in UTC days of the oldest, or `none` when there is none.
- `BACKLOG_OPEN`: lines `- status: open` or `- status: proposed` in the `## Entries` section, outside HTML comments.
- `MSGS_UNCLAIMED_14D`: ccmsg messages with status `sent` or `read` sent more than 14 whole days ago.
- `DENIES_7D`: hook telemetry events with decision `deny` in the last 7 days.
- `DRIFT_PUSH`: the integer on the first line of `drift-push-count`.
- `RELIABILITY_OPEN`: markdown table rows in `~/.claude/skills/*/references/*.md` with a cell starting `Open - none fixed`.
- `SIGNAL_TOTAL`: the sum of the six counts above that are integers.
- `DAYS_SINCE_REVIEW`: UTC days since the last interactive review line, or `none`.

#### Scenario: Weekly run writes a snapshot
- **WHEN** the backlog has 2 Inbox lines (oldest 5 days) and 3 open entries, 2 old unclaimed messages, 3 denies, a drift count of 4 and 2 open reliability rows
- **THEN** the snapshot holds those values, `SIGNAL_TOTAL=16`, a collection timestamp, and no model was called

#### Scenario: A signal source is unavailable
- **WHEN** the ccmsg store or telemetry store does not exist, or the drift count file is missing or not an integer
- **THEN** that value is `unavailable`, every other value is written, the missing store is not created, and the command exits 0

#### Scenario: Backlog missing
- **WHEN** the backlog file does not exist
- **THEN** `INBOX_COUNT`, `INBOX_OLDEST_DAYS` and `BACKLOG_OPEN` are `unavailable`

#### Scenario: Impossible Inbox date
- **WHEN** an Inbox line is dated `2026-02-31`
- **THEN** it is not counted

### Requirement: State files carry counts only
The snapshot, review log, stamps, summary lines and any nudge or notification text SHALL contain only counts, ages, dates, statuses, costs and fixed text. They SHALL NOT contain message subjects, message bodies, transcript text or personal identifiers.

#### Scenario: Unclaimed message with a sensitive subject
- **WHEN** an unclaimed message's subject names a person or a legal matter
- **THEN** only its count reaches the snapshot and the output, and its subject appears in no state file

### Requirement: The collector prints one summary line with both health results
The collector SHALL print exactly one line: `self-improvement: <total> signals (inbox <n>[/<oldest>d], entries <n>, msgs <n>, denies <n>, drift <n>, reliability <n>), last review <days>d|none, health PASS|FAIL, auto-review PASS|FAIL`. When the loop is not set up, the line SHALL end with `, loop not set up (run ccst self-improvement init)`.

#### Scenario: Summary line
- **WHEN** the collector runs with the last review 10 days ago and 16 signals
- **THEN** it prints one line containing `self-improvement: 16 signals`, `last review 10d`, `health PASS` and `auto-review PASS`

#### Scenario: Loop not set up
- **WHEN** the collector runs and no backlog exists
- **THEN** its one line ends with `loop not set up (run ccst self-improvement init)`

### Requirement: Loop health is PASS or FAIL
`LOOP_HEALTH` SHALL be `PASS` only when the last interactive review was at most 21 days ago and the Inbox is empty or its oldest line is at most 30 days old; otherwise `FAIL`, including when the backlog is unreadable or no review is recorded.

#### Scenario: Boundaries
- **WHEN** the last review is 21 days old, or the oldest Inbox line is 30 days old with a recent review
- **THEN** loop health is PASS; at 22 days, or an Inbox line 31 days old, it is FAIL

### Requirement: Auto-review health confirms the headless review runs
`AUTO_REVIEW_HEALTH` SHALL be `PASS` only when the review log has a `mode=auto` line with status `ran` or `skipped` dated at most 8 days ago; otherwise `FAIL`, including before the first headless run.

#### Scenario: Job silently stopped
- **WHEN** the newest `auto` line is 9 days old, or the only recent one has status `failed`
- **THEN** auto-review health is FAIL

#### Scenario: Job is running
- **WHEN** an `auto` line with status `skipped` was written 3 days ago
- **THEN** auto-review health is PASS

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

### Requirement: Unreadable or future-dated state never stops a run
A state file, backlog or skill reference that cannot be read SHALL be treated as missing, and undecodable bytes SHALL be replaced rather than fail the read. A date in the future SHALL count as no usable date: it SHALL NOT make a review, a headless run or an escalation count as recent.

#### Scenario: Undecodable byte in the review log
- **WHEN** `reviews.log` contains an invalid UTF-8 byte next to a valid interactive line 4 days old
- **THEN** the collector records 4 days since the last review and exits 0

#### Scenario: Future-dated review
- **WHEN** the only interactive and `auto` lines are dated 2 days in the future
- **THEN** days since the last review is `none`, both health results are FAIL and the escalation is sent

### Requirement: Reviews are read from a shared review log
The review log `reviews.log` SHALL be read as written by the interactive review skill (`<ISO time> mode=<B|C> findings=<n> applied=<n> deferred=<n>`) and by the headless review (`<ISO time> mode=auto status=<ran|skipped|failed|over-budget> sessions=<n> proposals=<n> cost_usd=<x|unknown> duration_s=<n>`). Only `mode=B` and `mode=C` lines SHALL count towards days since the last review; `mode=auto` lines SHALL NOT reset it.

#### Scenario: Only headless runs since the last interactive review
- **WHEN** the last interactive review was 20 days ago and a headless run logged yesterday
- **THEN** days since the last review is 20

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

### Requirement: The nudge does not duplicate the config repository's own nudge
The hook SHALL emit nothing when the user settings file registers a SessionStart command whose path ends in `self-improvement-nudge.sh`, because that bash hook shows the same nudge from the same state files.

#### Scenario: Bash nudge also installed
- **WHEN** `~/.claude/settings.json` has a SessionStart command `bash /some/path/hooks/session-start/self-improvement-nudge.sh` and a review is due
- **THEN** the CCST hook emits nothing and writes no stamp

### Requirement: Nudge preview and status commands
`ccst self-improvement nudge` SHALL print the nudge line when a review is due, or `no review due`, ignoring and never writing `last-nudge`. `ccst self-improvement status` SHALL print whether the loop is set up (and, when it is not, that `ccst self-improvement init` enables it), that an interactive review is run with `/review-session-and-improve-claude-setup periodic`, the snapshot values, the last interactive review line, the last headless line and the headless cost since the last interactive review, and SHALL write nothing.

#### Scenario: Preview does not consume the daily nudge
- **WHEN** a review is due and `ccst self-improvement nudge` runs
- **THEN** it prints the nudge line and `last-nudge` is unchanged

#### Scenario: Status before setup
- **WHEN** no backlog exists and `ccst self-improvement status` runs
- **THEN** it prints that the loop is not set up, names `ccst self-improvement init` and `/review-session-and-improve-claude-setup periodic`, and creates nothing

### Requirement: The headless review runs at most once a week
`ccst self-improvement auto-review` SHALL log `skipped` and make no model call when, without `--force`, the review log has an `auto` line dated less than 7 days ago whose status is `ran` or `over-budget`, or `failed` with a cost other than `0`. It SHALL then refresh the snapshot with escalation off, and SHALL log `skipped` with cost 0 and make no model call when the signal total is 0 and no transcript changed since the last completed run.

#### Scenario: Run limit reached
- **WHEN** a `ran` line is 3 days old and the job runs without `--force`
- **THEN** no model call is made and a `skipped` line with cost 0 is logged; with `--force`, or with the `ran` line 7 days old, the model is called

#### Scenario: Failed run that may have spent money
- **WHEN** the newest `auto` line is `failed` with cost `0.30` or `unknown` 2 days ago
- **THEN** no model call is made; with cost `0` the model is called

#### Scenario: Nothing new
- **WHEN** the signal total is 0 and no transcript is newer than `last-auto-review`
- **THEN** no model call is made and a `skipped` line with cost 0 is logged

### Requirement: The headless digest is bounded and private
When it runs, the job SHALL build the digest in a private temporary directory (mode 0700) that is removed when the run ends, whether it succeeds or fails. The digest SHALL hold today's UTC date, the snapshot, the backlog Inbox and open or proposed entries, a count of deny events in the last 7 days by hook, the `friction_detail` and `outcome` of any facet files in `~/.claude/usage-data/facets/` (a missing or empty directory is not an error), and extracts of at most 15 transcripts under `~/.claude/projects/` changed since the last completed run (or in the last 7 days before the first), newest first. Extracts SHALL contain only user-message text (500 characters each), tool errors (300 characters each) and nothing from assistant messages, successful tool results or system-injected text, capped at 8000 bytes per session. Transcripts under a `subagents/` directory or marked `"entrypoint":"sdk-cli"` in their first 20 lines SHALL be excluded. The digest SHALL be capped at 122880 bytes.

#### Scenario: Many large transcripts
- **WHEN** 20 large transcripts, one headless transcript and one subagent transcript changed since the last run
- **THEN** the model input after the prompt is at most 122880 bytes, holds at most 15 sessions, excludes the headless and subagent transcripts and assistant text, and includes tool errors

#### Scenario: Digest cleanup after a failed run
- **WHEN** a run fails
- **THEN** its temporary directory no longer exists

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

### Requirement: The automated parts propose and never apply
The collector, nudge hook, preview and status commands SHALL write only files in the state directory. The headless review SHALL write only its state files and lines appended to the backlog Inbox. None of them SHALL edit any `CLAUDE.md`, skill, hook or settings file.

#### Scenario: Collector run
- **WHEN** the collector runs
- **THEN** no file outside the state directory changes

#### Scenario: Headless review run
- **WHEN** a headless review completes with proposals
- **THEN** the only files changed are state files and the backlog

### Requirement: The loop stays quiet until it is set up
The loop SHALL count as set up only when `~/.claude/self-improvement-backlog.md` (a symlink is followed) can be read and has a line exactly `## Inbox`. Until then the nudge hook SHALL emit nothing, `ccst self-improvement auto-review` SHALL make no model call, send no notification, log one `mode=auto status=skipped` line with cost 0 and print a summary naming `ccst self-improvement init`, and the collector SHALL send no escalation. None of them SHALL create the backlog. The summary lines of both commands SHALL be at most 1000 characters, so the scheduler's surfaced weekly digest shows them whole, including the `ccst self-improvement init` hint.

#### Scenario: CCST-only install, weekly jobs run
- **WHEN** no backlog exists and both jobs run
- **THEN** no model is called, no Telegram message is sent, the review log gains one `status=skipped` line with `cost_usd=0`, no backlog is created, and each job's one output line names `ccst self-improvement init`

#### Scenario: Backlog without an Inbox heading
- **WHEN** the backlog exists but has no `## Inbox` line
- **THEN** the run behaves as for a missing backlog

### Requirement: Interactive reviews are recorded through CCST
`ccst self-improvement record-review --mode <B|C> --findings <n> --applied <n> --deferred <n>` SHALL append exactly one line `<UTC ISO 8601 time with Z> mode=<B|C> findings=<n> applied=<n> deferred=<n>` to `reviews.log` in the state directory (creating the directory when missing, following `CCST_DATA_HOME`), print that line and exit 0. Every option SHALL be required; a mode other than `B` or `C`, or a count that is not a non-negative integer, SHALL be rejected with exit 2 and nothing written. The headless review and this command SHALL append lines through the same helper.

#### Scenario: Recording a periodic review
- **WHEN** `ccst self-improvement record-review --mode C --findings 3 --applied 1 --deferred 2` runs
- **THEN** `reviews.log` ends with a `mode=C findings=3 applied=1 deferred=2` line dated now, and days since the last review is 0

#### Scenario: Invalid count
- **WHEN** `--findings -1` or `--mode auto` is given
- **THEN** the command exits 2 and `reviews.log` is unchanged
