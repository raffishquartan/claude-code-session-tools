---
name: clean-hook-sessions
description: Archive and delete Claude Code hook-security-check session transcripts — those whose first user message contains "Review this shell command for security risks" (the `bash-security-review` hook's own sub-sessions). These sessions pile up fast and pollute the `claude --resume` / `--continue` menu. Dry-run by default; `--execute` required to actually archive and delete. Defaults - age threshold 7 days (`--older-than DAYS`), keep-floor 10 most-recent sessions (`--keep-n N`), archive dir `~/.claude-projects-archive/`. Safety sequence - dry-run first → user sees count, sizes, date range; 8-digit confirmation code required (via the generate-8digit-code skill); tar.gz backup written to archive dir and verified before any deletion; deletion proceeds only if verification passes. Use when the user says "clean up hook sessions", "archive old hook security sessions", "remove hook sessions older than N days", "clear out security-check sessions", or reports that `claude --resume` / `--continue` is cluttered with security-check conversations.
---

# Clean hook sessions

Claude Code's `hook-security-check` subagent — spawned by CCST's own `bash-security-review` hook — runs on every Bash command. Each invocation creates a session transcript at `~/.claude/projects/<slug>/<uuid>.jsonl` whose first user message begins with "Review this shell command for security risks". These transcripts pile up by the thousands and pollute the `claude --resume` / `claude --continue` menu.

This skill archives and deletes those transcripts. It does NOT touch normal user sessions. CCST also bundles a weekly `clean-hook-sessions-weekly` ccsched job (`ccst ccsched-jobs install`) that runs this script unattended with `--execute` — see `lib/scheduler/bundled_jobs.py`. This skill is for an on-demand, interactive run outside that cadence.

## Detection rule

A session file is a hook-security-check transcript if, within the first 50 lines of its jsonl, the first `type: user` entry's text content contains the literal substring:

    Review this shell command for security risks

Anything else is treated as a normal session and left alone.

## When to use

- User says "clean up hook sessions", "archive old hook security sessions", "remove hook sessions older than a week".
- User complains that `--resume` shows thousands of security-check conversations.
- Periodic maintenance, e.g. weekly or monthly (already covered by the bundled `clean-hook-sessions-weekly` job on any machine that ran `ccst ccsched-jobs install --apply`).

## How to invoke

Run `scripts/clean-hook-sessions.py` from this skill directory. All arguments are optional except `--execute` for actual deletion.

| Flag | Default | Purpose |
|---|---|---|
| `--older-than DAYS` | `7` | Only consider files whose mtime is older than this many days. Floating-point accepted. |
| `--keep-n N` | `10` | Always keep the N most recent hook sessions, regardless of age. |
| `--execute` | off | Without this, the script runs in dry-run mode and prints what it would do. With this, it creates the tar.gz and deletes. |
| `--archive-dir PATH` | `~/.claude-projects-archive` | Directory where the tar.gz backup is written. Created if missing. |
| `--projects-dir PATH` | `~/.claude/projects` | Root directory to scan for jsonl transcripts. |

## Recommended usage

1. First run **dry-run** with the user's requested age threshold:

        python3 ~/.claude/skills/clean-hook-sessions/scripts/clean-hook-sessions.py --older-than 7

2. Show the user the summary (count, size, oldest/newest timestamps of what would be deleted).

3. **8-digit confirmation required.** Use the `generate-8digit-code` skill to get a cryptographically random code (never invent one yourself), present it to the user, and require their next message to be exactly and only that number before proceeding. Do not run `--execute` until this confirmation is received.

4. Then run with `--execute`:

        python3 ~/.claude/skills/clean-hook-sessions/scripts/clean-hook-sessions.py --older-than 7 --execute

5. Confirm the backup tar.gz path from the output and state the count deleted.

## Safety guarantees

- **Dry-run by default.** `--execute` is mandatory to touch the filesystem.
- **Backup first, then delete.** The script creates the tar.gz, verifies the archive contains the expected number of files, then deletes. If the archive creation or verification fails, no files are removed.
- **Keep-floor.** The N most recent hook sessions are always preserved, regardless of age threshold.
- **Only hook sessions.** Non-matching files are never touched.
- **No-op returns 0 and explains.** If zero files match, the script exits cleanly with a message - no error, no empty archive.

## What this skill does NOT do

- Does not touch `~/.claude/sessions/*.json` (short metadata records). Those are tiny and not the source of the clutter.
- Does not touch subagent files in `/subagents/` subdirectories.
- Does not attempt to identify or delete other session categories (slash-command probes, aside-questions, etc.). If needed, extend with additional detection rules rather than bolting on here.

## File layout

    ~/.claude/skills/clean-hook-sessions/
      SKILL.md                                 (this file)
      scripts/
        clean-hook-sessions.py                 (implementation)
