## Why

Measured over one hour on one machine (2026-09-19, indicative token weights), the
`bash-security-review` hook's Tier 3 spawned 114 separate `claude -p --model sonnet` sessions - about
46% of the window's total usage - and contributed to exhausting the 5-hour allowance. 113 of those
fires were cache misses against 1 hit: `CCST_USE_COMMAND_CACHE=1` was on, but the commands were
unique one-off invocations of the same few repo scripts with different arguments (for example
`python3 scripts/correspondence_log.py add --timestamp ... --subject ...`), so an exact or normalised
command cache never repeats. Each fire's first request carried ~118k-130k tokens for a one-line
review, because the `claude -p` child loads everything a normal session does: global and project
`CLAUDE.md` plus imports (~230k chars), the skill listing, deferred-tool listings and SessionStart
hook context.

Two independent levers, requested via `ccmsg` `20260919T231009Z-4ce1`:

- **A.** make each unavoidable review cheap.
- **B.** stop reviewing repeat invocations of scripts that have already been reviewed and are unchanged.

## What Changes

**A - slim `claude -p` call** (`call_claude` in `src/hooks/bash_security_review.py`)
- Measure first-request tokens per fire, before and after, for candidate slimming mechanisms:
  `--disable-slash-commands`, `--strict-mcp-config`, `--setting-sources` limited, a custom
  `--system-prompt` / `--exclude-dynamic-system-prompt-sections`, running the child from an empty
  working directory (so no project `CLAUDE.md` is discovered) and pointing it at a minimal config
  directory via `CLAUDE_CONFIG_DIR` (so no global `CLAUDE.md`, skills or hooks load).
- Adopt the combination that meets the acceptance bar in design.md with the SUMMARY / RISKS / VERDICT
  output unchanged on a fixed command corpus. `--bare` is out: it requires `ANTHROPIC_API_KEY` and
  ignores OAuth, and this setup runs on a subscription.

**B - configurable allowlist of reviewed scripts**
- New store of allowlisted scripts (path + sha256 of content) in the existing hook database,
  with a `ccst hooks allowlist` subcommand (`list`, `add`, `remove`, `verify`).
- New tier between the heuristic tier and the command cache: a command that is a single, simple
  invocation of an allowlisted script whose current hash matches is allowed without an LLM call,
  regardless of arguments.
- Auto-add after a Tier 3 `safe` verdict when: the review saw the script's content; the script is
  inside the project (git root) and not under `cc-sessions/`; no heuristic flag fired; and the
  invocation is a recognised simple form.
- Tier 1 heuristic hits still always escalate and are never allowlisted. A hash mismatch falls
  through to a normal review and is reported to the user.
- Reviews of script invocations now include the script's content (size-bounded) so a `safe` verdict
  is about the script, not just its command line.

## Capabilities

### New Capabilities
- `hooks/security-review-slim-call`: constraints on the context the review child loads, and the
  behaviour-preservation guarantee.
- `hooks/reviewed-script-allowlist`: allowlist storage, matching rules, auto-add rules, hash-mismatch
  handling, management CLI, telemetry.

### Modified Capabilities
(none - the existing hook has no spec yet)

## Impact

- `src/hooks/bash_security_review.py`: `call_claude` invocation; new allowlist tier in `run`;
  `build_prompt` includes script content for script invocations.
- `src/hooks/cache.py` (or a sibling module): new `script_allowlist` table alongside `command_cache`
  and `hook_invocations` in `command-cache.db`; DDL is additive (`CREATE TABLE IF NOT EXISTS`).
- `src/cc_session_tools/cli/ccst.py`: `hooks allowlist` subcommands (data-store convention: a store
  ships with its query subcommand).
- `README.md` and hook docstring: tier list updated.
- Tests: hook tier ordering, matcher forms, shell-structure rejection, hash mismatch, auto-add
  exclusions, telemetry, plus a measurement script for A (not run in CI - needs a live `claude`).
- Minor version bump (additive: new tier, new subcommand, new table; no existing on-disk data changes).

## Non-goals

- Changing Tier 0 / Tier 0.5 (see design.md, "Observation about Tier 0").
- Reviewing the script's imports or sibling modules; the hash covers the entry script only.
- Rewriting the cache normaliser.
