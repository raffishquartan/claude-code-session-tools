---
name: context-override
description: Silence the context-window-warning Stop hook's 150k/200k warnings entirely for the current session. Use when the user runs /context-override, or says "stop the context warnings", "silence the compact nag", "I know about the context, stop warning me". By default the warnings are visible but non-blocking; this makes them silent. Run with "off" to re-enable warnings, "status" to check.
---

# context-override

Toggles a per-session flag that the `context-window-warning` Stop hook checks.
When ON, the hook still surfaces a gentle "⚠ Context ~Xk" reminder each turn but
never pauses work, so file reads and other tool calls proceed normally past the
150k/200k thresholds.

## When to use

- The user types `/context-override` -> default to turning it **ON**.
- The user says "stop the context warnings", "silence the compact nag", or otherwise
  wants the per-turn warnings to stop appearing.
- Note: warnings are non-blocking by default. Override silences them entirely - useful
  when the user is aware of the situation and doesn't want per-turn noise.
- `/context-override off` (or "re-enable context warnings") -> turn it **OFF**.
- `/context-override status` -> report the current state.

## How it works

The flag lives in a `context_overrides` row in CCST's shared `sessions.db`, keyed by
`$CLAUDE_CODE_SESSION_ID`. The Stop hook (`ccst hooks run context-window-warning`) reads
that state and, when `on`, exits silently - no warning is injected and the session stops
normally. Warnings are visible but non-blocking by default; override makes them completely
silent. The override does not persist across sessions - a new session starts with
warnings active again. Running `/compact` does not clear it; use
`/context-override off` to re-enable warnings mid-session.

## Steps

1. Pick the action from the user's words: `on` (default), `off`, or `status`.
2. Run:

   ```bash
   ccst context-override <action>
   ```

3. Relay the command's output to the user as a single confirmation line.
