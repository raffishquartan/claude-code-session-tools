## Context

See proposal.md (Why). The previous change (`2026-11-26-self-improvement-port`) shipped `ccst self-improvement` and listed follow-ups for job registration. Relevant current state:

- `lib/scheduler/bundled_jobs.py` is the single list of bundled jobs; `ccst ccsched-jobs install` (one of `install-everything`'s steps, so it runs on every version-change auto-sync) registers missing ids, reports hand-edited ones as "changed (not touched)", and records every id it sees as CCST-installed, so a later `ccsched remove` reads as a deliberate deletion that is never re-added without `--reinstall`.
- A config repository registered the same two job ids locally with `bash <path>/scripts/self-improvement/{collect-signals,run-headless-review}.sh`.
- A CCST-only install has no backlog, so the headless review logged `failed` (cost 0) and notified weekly, and the nudge woke up as soon as the collector created the state directory.

## Goals / Non-Goals

**Goals:**
- Both jobs on by default, and silent on every install until the user runs `ccst self-improvement init`.
- An existing bash-script registration is never silently replaced and has a two-command migration.

**Non-Goals:**
- Shipping the interactive review skill or the drift-count hook (owned by the config repository).
- Changing the shared state-file formats.

## Decisions

### D1. "Loop set up" means a backlog with a `## Inbox` heading
One predicate (`state.loop_set_up()`), used by the nudge, the headless review, the collector's escalation, `status` and the doctor check. The previous rule for the nudge ("state directory or backlog exists") is dropped: once the signals job is bundled, the collector creates the state directory on every install within a week, which would have woken the nudge everywhere. The backlog is the one thing only the user (via `init`) or the config repository's tooling creates, and without an Inbox the review has nowhere to put proposals, so it is the natural opt-in marker.
Alternative: register the jobs only when the backlog exists. Rejected: registration happens at install sync time, before the user has had a chance to opt in, and would need a second sync after `init`.

### D2. Not set up: `skipped`, cost 0, no notification
Follow-up (a). The headless review checks the predicate first, before the run limit and before refreshing the snapshot, and logs `mode=auto status=skipped ... cost_usd=0` with a summary naming `ccst self-improvement init`. `skipped` (not a new status) keeps the shared `reviews.log` format; it is not a paid run, so it never blocks a later one. The old "no backlog Inbox" `failed` + notification path is gone; an Inbox that disappears while the model runs is still `failed` (money was spent, the user should know). The collector still writes `signals.env` (counts are harmless and `status` shows them) but never escalates, and its summary line ends `, loop not set up (run ccst self-improvement init)`. Nothing creates the backlog except `init`.

### D3. `ccst self-improvement init`
Creates the backlog with `open(..., "x")` (exclusive create), so a concurrent writer or an existing file is never truncated; a dangling symlink counts as existing and is not followed into a new file. Content: a `# Self-improvement backlog` title, an empty `## Inbox` and an empty `## Entries` section (the two sections the collector and the review read). Existing file with an Inbox: exit 0, unchanged. Existing file without one: exit 1, unchanged, with the line to add.

### D4. The review command in messages
Follow-up (b). `signals.review_command()` returns `/review-session-and-improve-claude-setup periodic` when `~/.claude/skills/review-session-and-improve-claude-setup/SKILL.md` exists, else `ccst self-improvement status`. Used by the nudge and the escalation. The text is still fixed (one of two constants), so the counts-only rule holds. `status` prints the same hint, so the pointer leads to something that explains the state.

### D5. Strip list
Follow-up (c). `api_keys.API_AUTH_VARS` stays the one list `without_api_auth` removes, now built from named groups, checked against the strings in the `claude` 2.1.332 binary:
- credentials: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR`, `CLAUDE_CODE_GATEWAY_TOKEN`, `CLAUDE_CODE_GATEWAY_TOKEN_FILE_DESCRIPTOR`, `ANTHROPIC_FOUNDRY_API_KEY`, `ANTHROPIC_FOUNDRY_AUTH_TOKEN`, `ANTHROPIC_AWS_API_KEY`, `ANTHROPIC_IDENTITY_TOKEN`, `ANTHROPIC_IDENTITY_TOKEN_FILE`;
- provider switches: `CLAUDE_CODE_USE_BEDROCK`, `_VERTEX`, `_FOUNDRY` (shared with `with_api_key`) plus `CLAUDE_CODE_USE_ANTHROPIC_AWS`, `CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD`, `CLAUDE_CODE_USE_GATEWAY`, `CLAUDE_CODE_USE_MANTLE`;
- routing: `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_API_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_BEDROCK_BASE_URL`, `ANTHROPIC_BEDROCK_MANTLE_BASE_URL`, `ANTHROPIC_VERTEX_BASE_URL`, `ANTHROPIC_FOUNDRY_BASE_URL`, `ANTHROPIC_FOUNDRY_RESOURCE`, `ANTHROPIC_AWS_BASE_URL`, `ANTHROPIC_GOOGLE_CLOUD_BASE_URL`, `CLAUDE_CODE_CUSTOM_OAUTH_URL`.

`CLAUDE_CODE_OAUTH_TOKEN` is kept (it is a subscription token). `HTTP(S)_PROXY` is not stripped: the traffic stays TLS to the first-party API, and removing a corporate proxy would only make the run fail. `with_api_key` (the `ccdapi` launchers) is unchanged; its spec names its own list.
Alternative: a second list in `review.py`. Rejected: the previous change made this list single-sourced on purpose.

### D6. Bundled jobs and the bash-script registrations
Both jobs use the maintainer-chosen definitions, with `surface=True` for both (it matches the existing local registrations, so a migrated job behaves as before). `BundledJob` gains `replaces_scripts: tuple[str, ...]` (`collect-signals.sh`, `run-headless-review.sh`); `bundled_jobs.replaced_script(spec, job)` returns the script name when any command token's basename (after splitting each argument on whitespace, so a one-string `bash /x/y.sh` command matches too) is in that tuple.
- `ccst ccsched-jobs install`: such a registration prints `conflicting definition (not touched): <id> - runs <script>, which the bundled job replaces; to switch: ccsched remove <id>, then ccst ccsched-jobs install --apply`, and is **not** passed to `mark_bundled_installed`. Otherwise the migration would need `--reinstall`, because CCST would remember an id it never installed and treat its removal as a deliberate deletion.
- `ccst doctor`: `check_ccsched_job_registered` returns the same WARN before the generic field diff.

Alternative: replace the bash registration automatically. Rejected by the maintainer: report, do not replace. A user may still run the bash version on purpose.

### D7. Doctor loop check
Follow-up (e). `check_self_improvement_loop(backlog_path)` is WARN when either job is registered and enabled and the loop is not set up, OK otherwise (including when neither job is registered: there is nothing to warn about). An unreadable registry is WARN, matching `check_ccsched_job_registered`. WARN, not FAIL: nothing is lost, the loop simply idles.

### D8. Run-limit `skipped` counts as auto-review health PASS
Follow-up (d), left as is: the shared spec says `ran` or `skipped` within 8 days, and both implementations read the same log. A not-set-up `skipped` also counts as PASS, which is harmless because the collector does not escalate until the loop is set up.

## Risks / Trade-offs

- [Every install gets a weekly job that can spend subscription usage] → it makes no model call until `init`; afterwards at most one paying run per 7 days, capped at USD 0.50, documented in the README and `--help`.
- [A user with the bash registration never migrates] → `install-everything` and `ccst doctor` report it on every run; both versions read the same state, so nothing breaks meanwhile.
- [Users with a backlog but no review skill see weekly FAIL escalations] → the message points at `ccst self-improvement status`, which explains the state; `--no-escalate` remains.
- [A new `claude` routing variable appears] → the list is checked against one CLI version; the auth pre-check still fails closed for non-first-party logins.

## Migration Plan

The next sync after upgrading registers both jobs, or reports the existing bash registrations. To move from the bash versions: `ccsched remove self-improvement-signals`, `ccsched remove self-improvement-auto-review`, then `ccst ccsched-jobs install --apply`. To opt in on a fresh install: `ccst self-improvement init`. Rollback: `ccsched remove` the two ids (they will be reported as deleted, not re-added).
