---
name: find-claude-code-session
description: Use when locating a previous Claude Code session - "find my session about X", "what session was I in when Y", "search my cc-sessions for Z", "did I work on foo before", "/find-claude-code-session", "remind me what I called the session where I X". Also use proactively when the user references prior CC work without naming the session, so you can resume with `ccr` instead of starting fresh. Wraps the `ccs` CLI (already on PATH).
---

<!--
Copyright (c) 2026 Chris Fogelberg. All rights reserved.
Licensed for personal use only.
-->

# Find a Claude Code session

Locate a prior `cc-sessions/<YYYYMMDD>-<tag>/` directory by name or content, then offer the user a `ccr <fragment>` command to resume it.

This skill wraps the `ccs` CLI (`/home/chris/.local/bin/ccs`, on PATH). The CLI is the source of truth - this skill just teaches you when and how to call it.

## When to use

Triggers (run `ccs` immediately, do not ask follow-up questions first):

- "find my session about X" / "find the session where I X"
- "what session was I in when X"
- "search my cc-sessions for X"
- "did I work on X before" / "have I touched X in CC"
- "remind me what I called the session about X"
- "/find-claude-code-session [query]"
- User references prior CC work without naming the session (proactive use - look it up before asking them).

## When NOT to use - prefer `journal-search`

If the user asks **"what was I doing on date X"** with no specific CC session in mind, use `journal-search` (Evernote daily journal) instead. `ccs` finds the *session record*; `journal-search` finds *what the user did that day*. The user's daily journal is the broader record - sessions are only one kind of activity.

If unclear, use both: `ccs <date>` first (fast), and if it returns nothing, fall back to `journal-search`.

## The three search modes

| Mode | Command | Searches | Use when |
|------|---------|----------|----------|
| Name (default) | `ccs <query>` | Session basenames in `./cc-sessions/` | You remember a word from the tag |
| Contents | `ccs <query> --contents` | Text files inside each session in `./cc-sessions/` | You remember a phrase from notes/output, not the tag |
| Global | `ccs <query> --global` (with or without `--contents`) | All sessions in every project under `$CLAUDE_SESSION_TOOLS_REPO_ROOT` and `$CLAUDE_SESSION_TOOLS_PROJ_ROOT` (typically `~/repos/*` and `~/cc-claude-code/*`) | You can't remember which project the session was in |

All modes sort newest-first by the `YYYYMMDD` start-date prefix. Exit code is 0 on match, 1 on none.

## Handling roots-config errors

When `ccs --global` (or `ccs --contents --global`) exits non-zero, inspect stderr:

**If stderr contains `[CST-ROOTS-CONFIG-ERROR]`:**
The CLI detected a problem with the env-var configuration. The message itself states what to fix (e.g. "does not exist", "is a file, not a directory", or "No session roots configured"). Relay the full error to the user verbatim - it is self-explanatory. Do not try to work around it by rerunning with different args.

Common fixes the user may need:
- `CLAUDE_SESSION_TOOLS_REPO_ROOT` and/or `CLAUDE_SESSION_TOOLS_PROJ_ROOT` are not exported in `~/.bashrc`, or the shell that launched Claude Code did not inherit them.
- One of the env vars points at a path that no longer exists.

**If stderr does NOT contain `[CST-ROOTS-CONFIG-ERROR]`:**
The CLI returned an unrecognised error. Tell the user:

> `ccs` returned an unrecognised error. The `find-claude-code-session` skill may be out of date relative to the `cc-session-tools` CLI. Try `ccs --version` and `ccs --help`, then update the skill at `~/repos/claude-code-session-tools/skills/find-claude-code-session/SKILL.md` to handle the new error format.

## Recipe ladder - escalate, stop at first hit

```
1. ccs <short-substring>                              # name match, current project
   ↓ (no hits)
2. ccs <short-substring> --contents                   # content grep, current project
   ↓ (no hits)
3. ccs <short-substring> --global                     # name match, every project
   ↓ (no hits)
4. ccs <short-substring> --global --contents          # content grep, every project
```

Stop the moment a step produces hits. Don't run `--global` if local already answered - the global content grep is the slowest variant and pulls in unrelated noise.

## Query design

`ccs` does a **literal substring grep** (no regex). Pick distinctive substrings, not phrases:

- ✅ `ccs bashrc` - distinctive, will match `20260504-oneshot-improve-ccd-again` if "bashrc" appears in any tagged session or content
- ✅ `ccs improve-ccd` - first-term-after-project still works because tag convention is `<project>-<descriptor>`
- ✅ `ccs 202604` - date-prefix queries find every April 2026 session
- ✅ `ccs 20260504` - specific-day query
- ❌ `ccs "the session where I cleaned up bashrc"` - phrase queries almost never match a basename

The user's tag convention is `<project>-<descriptor>` (e.g. `oneshot-improve-ccd-again`, `cccs-config-cleanup`). The first descriptor word is usually enough. If they tell you the project, drop it from the query - `cd ~/repos/<project>` first, then `ccs <descriptor-word>`.

## Result formatting

Always present hits as something the user can paste into `ccr` to resume.

`ccr <fragment>` accepts any substring of a session basename, so the simplest copy-able fragment for each hit is the descriptor portion of the tag (e.g. for `20260504-oneshot-improve-ccd-again`, suggest `ccr improve-ccd-again`; the date and project prefix are usually redundant in `ccr`'s search space).

**One hit:**
```
Found `20260504-oneshot-improve-ccd-again` - resume with `ccr improve-ccd-again`.
```

**Multiple hits:**
```
Found 3 sessions matching `bashrc`:
  - 20260504-oneshot-improve-ccd-again - `ccr improve-ccd-again`
  - 20260420-cccs-bashrc-tidy           - `ccr bashrc-tidy`
  - 20260315-oneshot-bashrc-experiments - `ccr bashrc-experiments`
```

For `--global` results, `ccs` already appends `(~/path/to/project)` - keep that in your output, since the user needs to know which project to `cd` into before `ccr`.

For `--contents` results, the matching line(s) appear indented under each session name. Include them so the user can confirm which is the right hit.

**Zero hits after escalating to `--global --contents`:**
```
No sessions match `<query>`. Suggest:
  - try a different substring (current was `<query>`)
  - widen with `journal-search` if you're really after "what did I do" rather than "which session"
```

## What you do after finding the session

By default, just present the result and the `ccr` line. Do NOT auto-run `ccr` - that switches the user out of the current session.

If the user wants to do something *with* the found session in the current conversation (e.g. "summarise it for me"), the session contents are at `<project>/cc-sessions/<basename>/` - read working/, out/, and any WORKLOG.md from there.

## Common pitfalls

- **Forgetting `--global` when the user changed projects.** If `cwd` is wrong, name search will return nothing even when a perfect match exists. When in doubt, escalate to `--global` rather than declaring no match.
- **Running `--contents` first.** It's slower and noisier than name search. Always start with name search.
- **Treating the query as regex.** It's literal. `ccs '20260[34].*'` will match the literal string, not the pattern.
- **Quoting unnecessarily.** Single distinctive words don't need quotes.
- **Suggesting `ccr` with a too-short fragment that matches multiple sessions.** `ccr` will list candidates rather than resume - if your suggested fragment is ambiguous (e.g. just `oneshot`), use a longer one.
