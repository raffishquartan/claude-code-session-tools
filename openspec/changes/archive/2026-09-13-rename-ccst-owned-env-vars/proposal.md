## Why

CCST's hook-behavior environment variables are inconsistently prefixed: `CCST_SCREENSHOT_DIR` and
`CCST_NO_AUTO_SYNC` correctly use `CCST_` (CCST defines and parses them), but eleven others
(`ENFORCE_8DIGIT`, `MARKERS_DIR`, `ALLOW_STALE_WORKLOG`, `USE_COMMAND_CACHE`, `CACHE_DB`,
`CLAUDE_BIN`, `REVIEW_MODEL`, `REVIEW_TIMEOUT`, `HOOKS_DIR`, `FIRES_ACCESS`, and the
just-introduced `CONFIRM_8DIGIT_GATED_TOOLS`) are prefixed `CCCS_` even though CCST's own hook
code defines their meaning and is the only thing that parses them - CCCS's role is only to supply
a value, exactly as it does for `NOTIFY_EMAIL`/`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, which are
not `CCCS_`-prefixed at all. A prefix should track who owns the variable's contract (defines,
parses, documents its meaning), not which repo's config happens to supply a value in one
particular deployment - the `CCCS_` prefix on these eleven was an inconsistency, not a
deliberate design signal.

Note: `CCCS_ALLOW_MAIN` is a different case - it's parsed entirely by a CCCS-authored bash script
(`enforce-git-branch-policy.sh`), with no CCST involvement, so it correctly keeps its `CCCS_`
prefix and is out of scope for this rename.

## What Changes

- Rename all eleven CCST-parsed environment variables from `CCCS_*` to `CCST_*`:
  `CCST_ENFORCE_8DIGIT`, `CCST_MARKERS_DIR`, `CCST_ALLOW_STALE_WORKLOG`,
  `CCST_USE_COMMAND_CACHE`, `CCST_CACHE_DB`, `CCST_CLAUDE_BIN`, `CCST_REVIEW_MODEL`,
  `CCST_REVIEW_TIMEOUT`, `CCST_HOOKS_DIR`, `CCST_FIRES_ACCESS`,
  `CCST_CONFIRM_8DIGIT_GATED_TOOLS`.
- **BREAKING** in the narrow sense that a value already set under an old `CCCS_*` name stops
  taking effect - no backward-compatible fallback is added, since this configuration surface was
  introduced only in the last 24 hours (`cc-session-tools` 3.4.0) with a single real deployment
  (this session's own machine), which this same change also updates.
- No behavior change beyond the name - every default, parsing rule, and fallback stays identical.

## Capabilities

### Modified Capabilities
- `notify/confirm-8digit-gated-tools-config`: the requirement naming
  `CCCS_CONFIRM_8DIGIT_GATED_TOOLS` is updated to name `CCST_CONFIRM_8DIGIT_GATED_TOOLS` instead.
  The other ten renamed variables have no dedicated spec to update.

## Impact

- `src/hooks/*.py` (confirm_8digit, worklog_guard, catchup, bash_hard_deny, telemetry_trim,
  cache, telemetry, telemetry_query, markers, bash_security_review, pdata_sync) and
  `src/cc_session_tools/cli/ccst.py`, `src/cc_session_tools/lib/telemetry_store.py`,
  `src/cc_session_tools/lib/scheduler/ledger.py`,
  `src/cc_session_tools/skills/update-command-cache/scripts/update_command_cache.py`: rename the
  environment variable name each reads.
- `tests/`: matching rename in every test that sets or asserts on one of these names.
- `CHANGELOG.md`, version bump: patch (correcting a naming inconsistency in a
  less-than-24-hours-old configuration surface with one real deployment - not a compatibility
  break in practice).
- Companion change needed in `claude-code-config-sync` (separate repo, separate PR): rename the
  same key in `config/settings.json`'s `env` block and update `README.md`'s wording, plus the
  live `~/.claude/settings.json` on this machine - tracked as this change's own follow-up, not
  part of this PR's diff.
