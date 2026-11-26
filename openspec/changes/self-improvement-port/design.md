## Context

See proposal.md (Why). The behaviour being ported is a separate config repository's `self-improvement-loop` spec (ten requirements) and its design decisions, implemented there as bash scripts (`collect-signals.sh`, `run-headless-review.sh`, `lib.sh`, a fixed prompt) plus a bash SessionStart hook, covered by bats suites. Those scripts stay where they are; this change does not touch that repository.

Constraints that shape the port:
- The state directory `~/.local/share/claude/self-improvement/` and its flat-file formats are a shared contract: the config repository's review skill appends interactive lines to `reviews.log`, its drift hook writes `drift-push-count`, and its bash scripts may keep running against the same files until the follow-up change registers CCST's jobs.
- CCST already has a Telegram sender (`lib.scheduler.notify.send_telegram`, env or `~/.creds`), Python APIs for the ccmsg store (`messaging.service.list_messages`) and telemetry (`hooks.telemetry_query.query_events`), a hook registry and a default hook bundle.
- The headless model call must use the Claude Code subscription login only.

## Goals / Non-Goals

**Goals:**
- Behaviour and thresholds identical to the source spec, so the two implementations are interchangeable against the same state.
- Every bats case reproduced as a pytest case, with the model call faked by a stub `claude` executable on PATH.

**Non-Goals:**
- Registering the two scheduled jobs (follow-up change).
- Shipping the interactive review skill, the backlog file or the drift hook.
- Moving the state into SQLite.

## Decisions

### D1. Python modules, not a bash port
`src/cc_session_tools/lib/self_improvement/` holds `state.py` (paths, date helpers, review-log parsing, atomic write), `signals.py` (collector), `nudge.py` (due rule and message), `review.py` (headless review) and `prompt.py` (prompt loading and lever list). `ccst self-improvement <collect-signals|auto-review|nudge|status>` wraps them, and `src/hooks/self_improvement_nudge.py` is the hook.

Why: CCST has no bash-only constraint (that constraint exists in the source repository because its hooks must not depend on ccst). Python lets the collector call the ccmsg and telemetry APIs directly instead of parsing CLI output, removes the GNU/BSD `date`/`stat` differences the bash had to work around, removes the `jq` dependency, and is covered by the repo's pytest and mypy gates.
Alternative: ship the bash scripts as package data and run them. Rejected: two languages to test, and the scripts' only dependency on CCST would be shelling out to `ccst` again.

### D2. File formats are a compatibility contract, not a new data store
The repo convention says new stores use SQLite. This is not a new store: it is an existing set of flat files that other tools (the config repository's skill, drift hook and bash scripts) read and write, so the formats are kept byte-compatible: the same `signals.env` keys in the same order, the same `reviews.log` line shapes, `last-*` stamps holding a `yyyy-MM-dd` line (`last-auto-review` is an mtime stamp, as before). `ccst self-improvement status` is the query subcommand the convention asks for. Tests assert the exact formats.

### D3. Drift count contract
CCST does not write `drift-push-count`. It reads the first line when present and an integer, and records `unavailable` otherwise (missing file, other text), exactly as the source collector does. A CCST-only install therefore always shows `drift unavailable`; `unavailable` contributes 0 to the total. Any tool may write the file: one non-negative integer on the first line, replaced atomically.

### D4. Notifier
Reuse `send_telegram` (same credentials as the notify-user skill: `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from the environment or `~/.creds`). It returns `False` instead of raising, which is exactly the "a failed send never changes the outcome" contract. No new credential handling, and no dependency on the notify-user skill's script. Without credentials the send is a logged warning and the collector's stamp stays unwritten, so a later run retries. Messages keep the source wording.

### D5. Subscription-only model call
Before the model call the job runs `claude auth status --json` in the stripped environment and continues only when `loggedIn` is true, `apiProvider` is `firstParty` and `authMethod` is `claude.ai` (an interactive login) or `oauth_token` (a `CLAUDE_CODE_OAUTH_TOKEN` subscription token). Values checked on claude 2.1.332: an `ANTHROPIC_API_KEY` reports `api_key`, a Bedrock switch reports `third_party`/`bedrock`. Both the check and the call get an environment with `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` and `CLAUDE_CODE_USE_FOUNDRY` removed: the reverse of `api_keys.with_api_key`, sharing a single tuple of variable names with it. `--restricted` already ignores settings files, so an `apiKeyHelper` cannot apply. A failed check is `failed` with cost 0 (nothing was spent, so the 7-day limit does not apply) and is notified, so a lost login is visible within a week. There is never a fallback.
Alternative: skip the pre-check and rely on `claude -p` failing. Rejected: a call that fails without parseable JSON is logged with cost `unknown`, which blocks the next week's run although nothing was spent, and the check would not guard against a key slipping in through a variable we did not strip.

### D6. Nudge dedupe with the config repository's bash nudge
Both nudges read the same state and write the same `last-nudge` stamp, so sequential runs already show at most one nudge a day. Claude Code runs SessionStart hooks in parallel, though, so on one session start both could pass the stamp check. The CCST hook therefore reads `~/.claude/settings.json` and stays silent when any SessionStart command ends in `self-improvement-nudge.sh`. This is deterministic and needs no change in the other repository; once that repository removes its hook (after the job-registration follow-up), the CCST hook takes over without configuration. The message prefix is `[ccst]` so the source of a nudge is visible.

### D7. Paths that differ from the source
- Reliability rows: the source scanned its own `skills/*/references/*.md`. CCST scans `~/.claude/skills/*/references/*.md` (following symlinked skill directories), which covers skills installed by any tool.
- Prompt: bundled as `config/self-improvement-review-prompt.md` (already covered by `config/*` package data). Its lever list is parsed at runtime and is the single source of the accepted levers. The source's test that kept the prompt in line with the review skill's taxonomy cannot run here because CCST does not ship that skill; a test pins the parsed list instead.

### D8. Backlog missing: no model call
The source called the model even with no backlog and then failed the append, spending money for nothing. The port checks for the backlog and its `## Inbox` heading before the model call and logs `failed` with cost 0 (notified, not blocking). This is the one behavioural difference, and it only applies to installs without a backlog.

### D9. Process control
`subprocess.Popen` with `start_new_session=False`, a poll loop on a monotonic clock, SIGTERM at the limit, SIGKILL after a 3 s grace, then `wait`. Timeouts come from `SELF_IMPROVEMENT_MODEL_TIMEOUT_S` and `SELF_IMPROVEMENT_JOB_BUDGET_S` (names kept for compatibility). The digest directory is a `tempfile.TemporaryDirectory` (0700), so cleanup holds on every path, including exceptions.

### What a CCST-only install misses
- The interactive review skill `review-session-and-improve-claude-setup` (Mode A/B/C) is owned by the config repository and is not shipped here under the same name. Without it, nothing writes `mode=B|C` lines, so loop health stays FAIL, the nudge fires daily once 30 days pass with "no review recorded", and the escalation repeats weekly. The nudge's `/review-session-and-improve-claude-setup periodic` command does not exist on such an install.
- The backlog file `~/.claude/self-improvement-backlog.md`: without it the Inbox counts are `unavailable` and the headless review refuses to run (D8).
- The drift count (D3).
These are listed as follow-ups for the maintainer; the job-registration change should decide whether to bundle a skill (under a different name) and a backlog template, or to register the jobs only when the skill is present.

## Risks / Trade-offs

- [Both implementations write the same state while both are installed] → formats are identical and writes are atomic; the run limit reads the shared log, so two schedulers still spend at most once a week.
- [Parallel SessionStart hooks show two nudges] → D6.
- [`claude auth status` output changes] → only `loggedIn`, `apiProvider` and `authMethod` are read; an unknown value fails closed with cost 0 and a notification.
- [Telegram missing on a CCST-only install] → the collector's one-line output and the nudge still surface FAIL.

## Migration Plan

Additive. Installing the release adds the hook to the default bundle on the next sync; the subcommands are inert until run or scheduled. Rollback: `ccst hooks uninstall --hook self-improvement-nudge --apply`, or downgrade; the state directory is untouched by either.
