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

## Pending-rename marker accumulation and reminder noise

The `move-session` skill drops a `.pending-rename` marker into a session's
`cc-sessions/<tag>/` directory on any tag-changing (move/rename) operation.
The SessionStart hook (`skills/move-session/hooks/sessionstart-pending-rename.sh`)
then prints a per-marker reminder block on every session start. In practice
these accumulate (one project had 84) and the reminder becomes persistent
startup noise that the user stops acting on.

Surfaced by an external config-review session
(`20260620-claude-create-self-learning-skill`) as backlog item B-001.

### Pre-existing test failures (fix first)

`tests/test_hook.bats` has two failing tests against the current hook —
cosmetic wording drift, not behaviour:

- [ ] Test 4 ("surfaces a single marker...") expects the header
  `Pending session-rename markers found`; the hook emits
  `N pending session-rename marker(s) in this project`.
- [ ] Test 6 ("emits copy-pastable /rename and rm commands...") expects
  `INSIDE Claude Code`/`OUTSIDE Claude Code` (or `INSIDE CC`/`OUTSIDE CC`);
  the hook emits `Inside CC:`/`Outside CC:`.

Reconcile the hook and tests (pick one wording, update both) before adding
new behaviour.

### Proposed improvement — auto-prune fulfilled markers + terse reminder

- [ ] **Auto-prune fulfilled markers.** A marker is *fulfilled* once the
  session's picker label already matches its `tag`. The label lives as a
  `custom-title` record in the session transcript jsonl — the same signal
  `move_session.py:jsonl_summary()` already parses (keys: `title`,
  `customTitle`, `content`, `value`). The hook can resolve the transcript at
  `~/.claude/projects/<encoded-project-cwd>/<uuid>.jsonl` (encoding: replace
  `/` and `.` in the cwd with `-`) and silently delete any marker whose
  transcript contains a `custom-title` equal to the marker's `tag`.
- [ ] **Collapse the reminder.** For markers that remain, print a 2-line
  summary (count + the bulk-clear command) instead of the full per-marker
  block. Keep the per-marker detail behind a flag or only when the count is
  small (e.g. ≤ 3).
- [ ] **Add a prune test** to `tests/test_hook.bats` (marker + matching
  transcript custom-title → marker deleted, no output; marker + no match →
  surfaced).

### Caveat — auto-prune alone will not clear the existing backlog

Markers are written only on a *move*. After a move, the transcript's
`custom-title` is the *old* (creation-time) tag, not the new one, unless the
user ran `/rename`. So for moved-but-never-renamed sessions there is no
matching `custom-title` and the marker is *not* fulfilled — auto-prune will
correctly leave it. Clearing a large existing backlog of never-renamed
markers is a separate, explicit one-shot action
(`find ~/cc -name .pending-rename -delete`), not something auto-prune should
do silently.
