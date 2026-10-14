## Context

`install_claude_md()` reports `REPLACED` when a block differs from the rendered template, whatever the reason. `ensure_synced()` runs it automatically after a version change and must never raise, never change the exit code, and never write to stdout. State shared across ccst commands lives in `~/.local/share/claude/sessions.db`, opened through the shared connection helper; the repo's data-store convention requires SQLite plus a query subcommand. See proposal.md - Why.

## Goals / Non-Goals

**Goals:**
- Never lose a customised managed block silently: keep its text and say so.
- Distinguish a hand edit from an out-of-date template without keeping every historical template.

**Non-Goals:**
- Preserving customisations by merging them into the new template.
- Blocking the overwrite (the template stays authoritative; users who want a permanent change must change the template, as with the WORKLOG paragraph).
- Detecting edits to text outside the sentinels (never touched).

## Decisions

- **Record a hash of the last-written block, not the text.** Equality with the recorded hash means "ccst wrote this and nobody changed it", which is all detection needs. Alternative: ship hashes of all historical templates; rejected as unbounded maintenance.
- **Store the hash in `sessions.db` (`claude_md_blocks`: path, section, sha256, updated_at).** The file's sentinel format stays as is, so older ccst versions keep reading it. Alternative: put the hash in the sentinel comment; rejected because it changes the on-disk format and would force a major bump under the repo's version policy.
- **Back up to a `claude_md_backups` table in the same database (id, taken_at, path, section, text) with `claude-md backups list|show`.** Follows the repo's SQLite-plus-query-subcommand convention. Alternative: timestamped files in a backup directory; rejected for violating that convention.
- **Treat "no record" as drift.** The first run after this ships backs up every differing block once, including blocks that were only out of date. Accepted: it errs towards keeping text, and the cost is one extra stored row per section.
- **Warn on stderr in the auto-sync path.** Respects the "never write stdout" invariant of `ensure_synced`.
- **Doctor check is read-only and uses the same comparison**, so it needs no state beyond the recorded hash.

## Risks / Trade-offs

- One-time false-positive backups after the upgrade → documented in the CHANGELOG and the warning text.
- A recorded hash is per file path; a CLAUDE.md moved or copied to another machine has no record there → treated as "no record", i.e. backed up, which is the safe direction.
- Backups grow without bound → each is a few KB and only taken on a real mismatch; no pruning in this change.
- A hand edit that is later reverted by hand to the exact template text is indistinguishable from untouched → harmless, nothing is lost.
