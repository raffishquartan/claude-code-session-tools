---
name: update-command-cache
description: Curate the SHA-256 command cache used by the bash-security-review hook. Reads recent safe-verdict fires from telemetry.db, identifies commands not yet cached, presents them for approval, and records approved ones via cccs_hooks.cache.cache_record(). Also supports manual --remove and --flip operations on existing cache entries. Use when the user says "update the command cache", "curate cached commands", "review cache fires", "promote fires to cache", "remove a cache entry", or notices the cache is stale or polluted.
---

<!--
Copyright (c) 2026 raffishquartan. All rights reserved.
Licensed for personal use only.
-->

# Update command cache

The `bash-security-review` hook (when run with `CCCS_USE_COMMAND_CACHE=1`) records `safe`-verdict fires into the command cache at `~/.cache/claude/logs/command-cache.db` (SQLite; the legacy `command-cache.csv` is retired). Auto-fill is conservative - only verdicts that came back `safe` from the claude CLI escalation are stored. This skill is the curation tool for that cache: it lets you sweep the telemetry log for safe fires not yet captured, vet them, and bulk-promote the ones you want.

## When to use

- "Update the command cache."
- "Sweep the fires log for cacheable commands."
- "Promote my recent claude-CLI safe fires to the cache."
- "Remove this cache entry."
- "Flip this entry from safe to suspicious."
- After tuning the heuristic flag list, when stale entries should be re-validated.

## How it works

The bash-security-review hook records every fire to `telemetry.db`. The cache (`command-cache.db`, SQLite) holds `safe`-verdict commands keyed by their SHA-256 hash so subsequent identical commands skip the claude CLI call.

### Relationship to bash-hard-deny

This skill and the `bash-hard-deny` hook (`src/cccs_hooks/bash_hard_deny.py`) share one convention: `CCCS_FIRES_ACCESS=1`. `bash-hard-deny`'s telemetry.db-block section documents this env var as the bypass that lets a legitimate reader touch the telemetry log; this skill's own gate (in `scripts/update_command_cache.py`, `cmd_list`) refuses to read `telemetry.db` unless the same variable is set.

The two are NOT structurally coupled — the hook cannot literally intercept this skill's Python-internal file read, so setting the variable is what actually unlocks the read on both sides. They stay consistent only by discipline: if `bash-hard-deny`'s bypass env var name or semantics ever change, this skill's gate must be updated by hand to match. It is a shared convention that must be kept in sync, not an enforced runtime dependency.

This skill:

1. Reads the fires log (gated by `CCCS_FIRES_ACCESS=1` so the bash-hard-deny hook permits the read).
2. Filters to entries with `verdict == "safe"` and `cache != "hit"` (i.e. ones that escalated to claude).
3. Cross-references each input hash with the current cache; drops anything already cached.
4. Presents the candidate list to the user with command preview, fire count, and last-seen.
5. After explicit approval, calls `cccs_hooks.cache.cache_record()` for each approved entry.

Manual modes:
- `--remove <sha>` removes a single entry (after confirmation).
- `--flip <sha> <verdict>` refreshes the entry (re-records it via `cache_record()`) when the new verdict is `safe`, or deletes it when the new verdict is non-safe.

## Invocation

From this skill's directory:

    CCCS_FIRES_ACCESS=1 python3 scripts/update_command_cache.py [--list] [--remove <sha>] [--flip <sha> <verdict>]

Default behaviour (no flag) is `--list`: show pending candidates and prompt for promotion.

## Safety notes

- Suspicious / dangerous fires are NEVER auto-promoted. Even via this skill.
- The skill never bypasses the SHA-256 exact-string match: a command in the cache must be byte-for-byte identical to the live command for the cache hit to fire. v2 may relax this once we have evidence of how big the near-miss class is.
- Promotions go through `cache_record`, which stores only `safe` verdicts and auto-prunes entries older than 90 days on every write. Removals and non-safe flips delete the row directly from the SQLite cache.

## Bottom line

This skill is the human-in-the-loop curation step for the auto-fill cache. Use it whenever `telemetry.db` has accumulated safe fires that you want to start short-circuiting on the next run, or when you need to surgically prune a bad entry.
