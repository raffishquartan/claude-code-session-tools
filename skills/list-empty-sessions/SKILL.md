---
name: list-empty-sessions
description: Use when the user wants to find sessions they never actually typed in. Triggers on "list empty sessions", "find sessions I never used", "which sessions are empty", "show me sessions with no messages", "show abandoned sessions", "find sessions with no user input", "which sessions did I never start", "/list-empty-sessions". Also use proactively when the user complains about session clutter from accidental ccd invocations or abandoned starts.
---

<!--
Copyright (c) 2026 raffishquartan. All rights reserved.
Licensed for personal use only.
-->

# List empty sessions

Find sessions whose transcript contains no user-typed messages — the kind of
sessions that accumulate from accidental `ccd` invocations, abandoned starts,
or one-shot sessions that were set up but never used.

## What "empty" means

A session is **empty** when its JSONL transcript contains zero messages that
the user actually typed. Specifically, the following do NOT count as
user-typed messages:

- Output from the `SessionStart` hook (marked `isMeta=true` in the JSONL).
- Slash-command invocations (content starting with `<command-name>`).
- System-reminder injections (content starting with `<system-reminder>`).
- Local command output (`<local-command-stdout>`, `<local-command-stderr>`).
- Tool result blocks (content blocks of `type: tool_result`).
- Compact-summary records (`isCompactSummary=true`).

If the JSONL transcript cannot be found at all, the session is treated as
empty (conservative default — `ccs` handles this the same way).

## When to use

- The user wants to see which sessions they never actually started working in.
- The user asks which sessions are safe to clean up.
- Upstream of `delete-sessions`: list empties first, then feed basenames to
  the delete skill.

## When NOT to use

- Searching for sessions by name or content — use the `find-claude-code-session`
  skill (wraps `ccs`).
- Deleting sessions — use the `delete-sessions` skill. This skill only lists;
  it does not modify anything.

## Scope

| What the user says | Scope |
|---|---|
| (nothing, or "in this project") | Local — current project only |
| "across all my projects" / "everywhere" / "globally" | Global — all configured roots |

Default is **local** (current project cwd). Escalate to global only when the
user explicitly asks.

## How to run

### Local (default)

```
python3 ~/.claude/skills/list-empty-sessions/scripts/list_empty_sessions.py
```

### Global

```
python3 ~/.claude/skills/list-empty-sessions/scripts/list_empty_sessions.py --global
```

The script is a thin wrapper around `ccs --emptiness only` (and
`ccs --emptiness only --global`). All session-discovery, JSONL-location, and
emptiness logic lives in `cc_session_tools` — the script just reformats the
output and adds the follow-up suggestions.

## Output format

One line per empty session:

```
<basename>                               # local
<basename>  (~/path/to/project)          # global — project path included
```

Sessions are grouped by project when running in global mode. A count summary
appears at the end:

```
4 empty sessions found (local).
```

If no empty sessions are found:

```
No empty sessions found (local).
```

## Follow-up suggestions

After the listing the script always prints two copy-pastable suggestions:

1. **Resume** any session to continue it:
   ```
   ccr <basename>
   ```

2. **Delete** all the listed empties at once by passing their basenames to the
   `delete-sessions` skill:
   ```
   python3 ~/.claude/skills/delete-sessions/scripts/delete_sessions.py \
       <basename1> <basename2> ...
   ```
   (Add `--execute` once you have reviewed the plan. The delete script is
   dry-run by default.)

## Common mistakes

- **Running globally when only the local project is cluttered.** Default to
  local. Global is slower and surfaces sessions from unrelated projects.
- **Confusing "empty" with "short".** A session with one real user message is
  NOT empty. `--emptiness only` applies strict criteria — hook output alone
  doesn't count.
- **Acting on the listing without reviewing.** This skill lists only. Pass
  basenames to `delete-sessions` with `--execute` to actually delete, and
  review the dry-run plan first.
