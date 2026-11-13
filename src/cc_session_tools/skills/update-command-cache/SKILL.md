---
name: update-command-cache
description: Curate the SHA-256 command cache used by the bash-security-review hook. Reads recent safe-verdict fires from telemetry.db, identifies commands not yet cached, presents them for approval, and records approved ones via hooks.cache.cache_record(). Also supports manual `remove` and `flip` subcommands on existing cache entries. Use when the user says "update the command cache", "curate cached commands", "review cache fires", "promote fires to cache", "remove a cache entry", or notices the cache is stale or polluted.
---

# Update command cache

The `bash-security-review` hook (when run with `CCST_USE_COMMAND_CACHE=1`) records `safe`-verdict fires into the command cache at `~/.local/share/claude/command-cache.db` (SQLite, overridable via `CCST_CACHE_DB`; the legacy `command-cache.csv` is retired). Auto-fill is conservative - only verdicts that came back `safe` from the claude CLI escalation are stored. This skill is the curation tool for that cache: it lets you sweep the telemetry log for safe fires not yet captured, vet them, and bulk-promote the ones you want.

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

This skill, `ccst telemetry recover`, and the `bash-hard-deny` hook (`src/hooks/bash_hard_deny.py`) share one convention: `CCST_FIRES_ACCESS=1`. `bash-hard-deny`'s telemetry.db-block section documents this env var as the bypass that lets a legitimate reader touch the telemetry log; this skill's own gate (in `scripts/update_command_cache.py`, `cmd_list`) and `ccst telemetry recover` each refuse to read `telemetry.db` or any transcript unless the same variable is set.

The gate is a convention, not an enforced runtime dependency. The hook cannot intercept this skill's Python-internal file read, and its rules do not match `ccst telemetry recover` at all, so setting the variable is what unlocks the read, checked by each reader itself. They stay consistent only by discipline: if `bash-hard-deny`'s bypass env var name or semantics ever change, both gates must be updated by hand to match.

This skill:

1. Reads the fires log (gated by `CCST_FIRES_ACCESS=1`).
2. Filters to entries with `verdict == "safe"` and `cache != "hit"` (i.e. ones that escalated to claude).
3. Cross-references each input hash with the current cache; drops anything already cached.
4. Prints each remaining candidate's sha, fire count, last-seen timestamp, a sample session id and
   a recovered command preview. `telemetry_events` stores only a SHA-256 `input_hash`, never the
   command text; the preview comes from `ccst telemetry recover`'s recovery (one batch pass over
   all candidates), which reports a command only when it re-hashes exactly to the candidate's
   hash. The preview is for triage only: it is cut to 200 characters, a cut preview ends with
   `…[+N chars]`, and newlines, tabs and other control characters are shown escaped (`\n`,
   `\t`, `\uXXXX`). A candidate whose command cannot be confirmed shows `status=session` (the
   hash fired in a known session but no transcript command matches it), `status=ambiguous` or
   `status=unknown` instead. `list --no-preview` skips recovery and opens no transcript.
5. Before promoting any candidate, show the full command:

       CCST_FIRES_ACCESS=1 ccst telemetry recover --max-chars 0 <sha>

   A cache entry approves the whole command, so a truncated preview is never enough to vet it.
   The output is one tab-separated line: hash as given, status, full 64-character hash, session
   uuid, `<transcript>:<line>`, command. Only status `command` confirms the plaintext; any other
   status means it could not be confirmed and the candidate must not be promoted on a guess.
6. Promote a vetted candidate with its full hash and the recovered command (as printed, escapes
   included) as the preview, with `$PY` set as under Invocation below:

       "$PY" ~/.claude/skills/update-command-cache/scripts/update_command_cache.py promote <full sha> --preview "<recovered command>"

End to end: `list` -> `ccst telemetry recover --max-chars 0 <sha>` -> `promote <full sha> --preview "<recovered command>"`.

Manual subcommands:
- `remove <sha>` removes a single entry (after confirmation).
- `flip <sha> <verdict>` refreshes the entry (re-records it via `cache_record()`) when the new verdict is `safe`, or deletes it when the new verdict is non-safe.
- `promote <sha> [--verdict safe] [--risks ...] [--preview ...]` records a single vetted entry directly.

**Note on synthetic rows:** `telemetry.db`'s `bash-security-review` rows can include synthetic
test-fixture rows (`session_id` values like `test-session-1`) alongside real production fires, if
this machine's test suite has ever run against the shared database. Don't be surprised if one of
these dominates the candidate list by fire count - check `sample_session` before promoting.

## Invocation

This script `import`s `hooks` and `cc_session_tools.lib` directly - it needs an interpreter that
actually has `cc-session-tools` installed, not a bare system `python3` (which will fail with
`ModuleNotFoundError: No module named 'hooks'`). Find that interpreter from whichever `ccst`
is already on `PATH` - its console-script shebang line names the exact venv Python that has the
package, regardless of install method (`uv tool install`, `pipx`, `pip --user`):

    PY="$(sed -n '1s/^#!//p' "$(command -v ccst)")"
    CCST_FIRES_ACCESS=1 "$PY" ~/.claude/skills/update-command-cache/scripts/update_command_cache.py list [--no-preview]
    CCST_FIRES_ACCESS=1 ccst telemetry recover --max-chars 0 <sha>
    "$PY" ~/.claude/skills/update-command-cache/scripts/update_command_cache.py remove <sha>
    "$PY" ~/.claude/skills/update-command-cache/scripts/update_command_cache.py flip <sha> <safe|suspicious|dangerous>
    "$PY" ~/.claude/skills/update-command-cache/scripts/update_command_cache.py promote <sha> [--verdict safe] [--risks ...] [--preview ...]

In a repo checkout (development), `uv run python3 scripts/update_command_cache.py ...` from this
skill's source directory works the same way.

Default behaviour (no subcommand) is `list` with previews: show pending candidates for triage.

## Safety notes

- Suspicious / dangerous fires are NEVER auto-promoted. Even via this skill.
- The skill never bypasses the SHA-256 exact-string match: a command in the cache must be byte-for-byte identical to the live command for the cache hit to fire. v2 may relax this once we have evidence of how big the near-miss class is.
- Promotions go through `cache_record`, which stores only `safe` verdicts and auto-prunes entries older than 90 days on every write. Removals and non-safe flips delete the row directly from the SQLite cache.

## Bottom line

This skill is the human-in-the-loop curation step for the auto-fill cache. Use it whenever `telemetry.db` has accumulated safe fires that you want to start short-circuiting on the next run, or when you need to surgically prune a bad entry.
