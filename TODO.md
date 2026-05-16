# TODO

Tracked follow-up work that is out of scope for the current release but
should land in a future one.

## notify-user skill (separate repo + CCST integration)

The `notify-user` skill currently lives outside this repo as a personal /
private skill. To make CCST's gated-action UX work properly for any user,
not just the original author, we need:

### Phase 1 — separate, public repo for the skill

- [ ] Create a new public repository (working name: `notify-user-skill`).
- [ ] MIT licence it.
- [ ] Ship a `SKILL.md` plus any helper scripts.
- [ ] Document **all** supported notification transports the skill knows how
  to talk to (Telegram bot, ntfy, Pushover, generic webhook, macOS
  `osascript`, etc.) and what credentials / environment variables each
  transport needs.
- [ ] Provide **detailed setup and configuration instructions** for
  newcomers: how to create the bot / channel / endpoint per transport, how
  to set the env vars, how to test the wiring, and how to revoke /
  rotate credentials.
- [ ] Include a smoke-test script (`notify-user --test`) that sends a
  one-off "hello from CCST" to verify the active transport works.
- [ ] The skill must be safe to install for users who have NOT configured
  a transport: it should no-op silently (or print a one-liner pointing at
  the setup docs) rather than crash.

### Phase 2 — CCST integration

Once the separate repo exists, update CCST to:

- [ ] **Install prompt** — when the user runs the global-CLAUDE.md
  bootstrap (`docs/global-claude-md-bootstrap-prompt.md`), prompt them to
  also install the `notify-user-skill`. Provide the one-line
  `ccst skills install --from-git <url>` (or symlink) command they need.
- [ ] **`ccst doctor`** — detect whether `notify-user` is installed and
  configured; surface a hint if it is missing.
- [ ] **`cccs_hooks.confirm_8digit`** — when the 8-digit gate fires AND
  `notify-user` is installed, send a push notification ("Claude Code wants
  to <action> in <session> — code is <NNNNNNNN>"). The user can then
  confirm from their phone instead of needing to be at the terminal.
- [ ] **Graceful degradation** — if `notify-user` is absent, the gate
  works exactly as it does today (terminal-only). No hard dependency.
- [ ] Update README to document the optional integration.

### Why this matters

The 8-digit confirmation skill blocks until the user types a code, which
forces them to be at the terminal. For long-running agents (subagents,
background tasks, /loop, scheduled routines) the user may be away from
the keyboard when a gated action fires. A push notification means the
agent does not silently stall.
