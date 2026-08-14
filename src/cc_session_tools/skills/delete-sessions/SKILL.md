---
name: delete-sessions
description: Use when the user wants to permanently delete one or more Claude Code sessions. Triggers on "delete session", "delete sessions", "remove empty sessions", "clean up sessions", "delete these sessions", "remove these cc-sessions", "/delete-sessions". Inputs must be explicit session basenames; the user (or the list-empty-sessions skill) must supply them. Gates every deletion behind an 8-digit confirmation code.
---

<!--
Copyright (c) 2026 raffishquartan. All rights reserved.
Licensed for personal use only.
-->

# Delete sessions

Permanently delete one or more Claude Code sessions: the `cc-sessions/<basename>/`
directory, the JSONL transcript, the `.tag` file, and (optionally) the
`~/.claude/tasks/<encoded>/` task directory.

**Dry-run by default.** The script prints a deletion plan and exits 0. Pass
`--execute` to actually delete. Always review the plan before executing.

**8-digit confirmation gate.** In `--execute` mode the script generates an
8-digit code, prints it, and requires the user to type it back exactly. The
deletion proceeds only if the code matches.

## When to use

- The user explicitly names one or more session basenames and asks to delete
  them.
- The `list-empty-sessions` skill has produced a list of basenames and the
  user confirms they want to remove them.

## When NOT to use

- Listing which sessions are empty — that is the `list-empty-sessions` skill.
  This skill deletes; it does not discover.
- Cleaning up hook-security-check transcripts — that is the
  `clean-hook-sessions` skill.
- The user wants to move or rename a session — use `move-session`.

## Inputs

Positional arguments only: one or more session **basenames** (the
`YYYYMMDD-<tag>` directory name under `cc-sessions/`).

```
python3 ~/.claude/skills/delete-sessions/scripts/delete_sessions.py \
    20260516-demo-empty 20260517-beta-empty
```

The script searches for each basename locally first (under the current
project's `cc-sessions/`), then across all configured roots. It does **not**
accept filesystem paths — pass the basename, not the full path.

## Pre-flight checks (all required — fail fast on any)

1. **Format check.** Every basename must match the session-name format
   (`YYYYMMDD-<tag>` pattern). If any does not, the script exits 1 immediately.

2. **Existence check.** Every basename must resolve to a real
   `cc-sessions/<basename>/` directory somewhere reachable. If any is missing,
   the script exits 1 with a list of what could not be found.

3. **In-session refusal.** If any of the named sessions is the *currently
   running* CC session, the script refuses outright with exit code 2. There is
   no override — see "In-session refusal" below.

4. **Empty-only guard.** By default the script refuses to delete non-empty
   sessions (those that contain at least one real user-typed message). Pass
   `--allow-non-empty` to bypass this. When blocked, the script lists which
   sessions are non-empty so the user knows what is preventing deletion.

## What is deleted (per session)

| Artefact | Path |
|----------|------|
| Session folder | `<project-cwd>/cc-sessions/<basename>/` |
| JSONL transcript | `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` |
| Tag mapping row | `~/.local/share/claude/sessions.db` — `session_tags` row keyed by `<uuid>`, removed via `sessions_db.delete_tag` (not a filesystem artefact) |
| Enumeration row | `~/.local/share/claude/sessions.db` — `sessions` row keyed by `(<project-cwd>, <basename>)`, removed via `sessions_db.delete_session_row` so the session stops appearing in `ccr`/`ccs` |
| Tasks directory | `~/.claude/tasks/<encoded-session-dir>/` (if present, skipped with `--no-tasks`) |

The dry-run plan prints the exact paths that would be deleted so you can
verify before committing.

## In-session refusal

If the user asks to delete the session they are currently inside, the script
exits 2 and explains why. There is no override flag.

**Detection signals** (same as `move-session`): refusal fires if `in_cc` AND
**either** (a) the session JSONL was modified within the last 30 seconds, OR
(b) `realpath(getcwd()) == session_project_cwd` AND the session JSONL is the
most-recently-modified non-hook-security JSONL in the project key directory.

## Quick reference

```
python3 ~/.claude/skills/delete-sessions/scripts/delete_sessions.py \
    [--allow-non-empty] \
    [--no-tasks] \
    [--execute] \
    <basename> [<basename> ...]
```

| Flag | Meaning |
|------|---------|
| `<basename>` | One or more `YYYYMMDD-<tag>` basenames. Required. |
| `--allow-non-empty` | Also delete sessions that contain real user messages. Still 8-digit gated. |
| `--no-tasks` | Skip `~/.claude/tasks/<encoded>/` deletion. |
| `--execute` | Actually delete. Without this flag the script only prints a plan. |

## 8-digit confirmation flow (--execute only)

1. The script prints the deletion plan.
2. It generates a fresh 8-digit code and prints it.
3. The user types the code back.
4. If the code matches exactly, deletion proceeds. If not, the script exits 1
   with no deletions made.

The model's role: present the code to the user with the line
`"Respond with <code> to confirm deletion."` and then pass the user's reply
into the script's stdin.

## Deletion behaviour

- Artefacts are deleted one at a time; each deletion is reported on its own
  line.
- On any `OSError` the error is printed and the script continues (best-effort
  for the remaining artefacts).
- Exit `0` if every artefact was either deleted or already absent.
- Exit `1` if any `OSError` occurred during deletion.

## Common mistakes

- **Forgetting `--execute`.** Default is dry-run. The plan is printed but
  nothing is deleted. Check the output for `DRY-RUN` before reporting success.

- **Trying to delete the currently-running session.** The script detects this
  and refuses with exit 2. Exit CC first, then re-run the script from a normal
  shell.

- **Trying to delete a non-empty session without `--allow-non-empty`.** If the
  JSONL contains even one real user message, the script refuses by default.
  This is intentional — non-empty sessions might contain valuable work. Review
  the session contents before using `--allow-non-empty`.

- **Confusing basename with just the tag portion.** The script expects the full
  `YYYYMMDD-<tag>` basename (e.g. `20260516-demo-session`), not just the tag
  (`demo-session`). The `list-empty-sessions` skill outputs full basenames
  ready for copy-paste.

## Defaults and safety

- **Dry-run by default.** Pass `--execute` to make filesystem changes.
- **Empty-only by default.** Pass `--allow-non-empty` to also delete sessions
  with real user messages.
- **8-digit gated.** All `--execute` runs require a confirmation code.
- **No source clobber recovery.** Deletion is permanent. Unlike `move-session`,
  there is no cleanup script — once you confirm, the files are gone.
