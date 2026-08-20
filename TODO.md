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

## Real dead-letter semantics for ccmsg

Build genuine dead-letter handling for `cc_session_tools/lib/messaging`:
messages that sit unclaimed past some age (e.g. 14 days) with no session ever
matching their recipient/description should be actively surfaced, not just
silently sit in the inbox forever. At minimum, this should send a message
back to the original sender explicitly flagging that their message has not
yet been received or processed. Beyond that, further handling (re-notify on
a cadence, auto-archive as undeliverable, etc.) is TBD — design it when
picked up.

Full background and the concrete design sketch (option 4.1.1.2, "Real
dead-letter semantics") is in:
`/mnt/c/Users/cfoge/OneDrive/claude/claude/cc-sessions/20260710-claude-identify-all-information-stored/working/investigation-notes-v2.md`

This TODO exists because the *current* `ccmsg-dead-letter-sweep` ccsched job
does not do this — it just re-runs the ordinary delivery sweep and has no
dead-letter logic at all (confirmed via code read, no matches for
`dead.letter` anywhere in the codebase). That job is being removed/renamed
separately; this TODO is the "build the real thing" follow-up if wanted.

## Pending-rename backlog: clearing the markers that are already there

Auto-prune (shipped: the hook drops markers whose transcript `custom-title`
already equals the marker's `tag`) does not touch the accumulated backlog.
Markers are written on a *move*, and after a move the transcript's
`custom-title` is still the creation-time tag unless the user ran `/rename` —
so a moved-but-never-renamed session has no matching title and its marker is
correctly kept. Clearing a large existing backlog (one project had 84) is an
explicit one-shot user action, not something auto-prune should do silently:

- [ ] Decide whether to clear the backlog wholesale
      (`find -L ~/cc -name .pending-rename -delete`, printed by the hook
      itself) or to work through the sessions and `/rename` them properly.

## Migration markers for ccmsg, ccsched and sessions

`ccst migrate telemetry` records an explicit marker (`migrations` table, via
`lib.db.MIGRATIONS_DDL`) when its one-shot import completes, and both the
script and `ccst doctor` read that marker to decide whether the import has
happened. The other three migrations still infer it from "does the new store
have any rows".

That inference is wrong for the same reason it was wrong for telemetry: the
new code writes to these stores from the moment CCST is installed, well
before anyone runs a migration. `sessions.db` gets `session_tags` rows from
the session-tag hook on the first session; `ccsched.db` gets job rows on the
first scheduler use; `ccmsg.db` gets rows on the first message. So a
`ccst doctor` run on a machine with unmigrated legacy data can report
`migration already ran` when it has not, and the SessionStart
`pending-migration` hook — which only surfaces FAILs — then stays silent
about it.

- [ ] Add `db.MIGRATIONS_DDL` to the ccmsg, ccsched and sessions store
  schemas.
- [ ] Record a marker at the end of each of the three migrations, inside the
  same transaction as the writes (see `migrate_telemetry.py` for why the
  marker must not be a separate commit).
- [ ] Switch `doctor.check_pending_data_store_migration` to the marker for
  all four stores and delete the `_count_new_store_rows` branch.
- [ ] Backfill: a store whose legacy sources are already gone has migrated by
  definition — decide whether to write the marker on first connect in that
  case, or leave it absent and rely on "no legacy data found -> OK".

## Harden `enforce-git-branch-policy.sh` against text-matching bypasses

Not a CCST file — the hook lives in `claude-code-config-sync`
(`hooks/pre-tool-use/enforce-git-branch-policy.sh`) — but tracked here since it surfaced during
CCST release work and CCST is where this project's cross-repo follow-up work lands. Full
write-up, evidence, and recommended fixes:
`~/cc/claude/enforce-git-branch-policy-hook-hardening.md`.

Found 2026-08-18 shipping the context-window-warning migration: the hook blocks mutating git
commands on `main`/`master` by scanning the raw Bash `command` string for verb keywords, rather
than parsing what actually executes. Proven with a false positive — a Telegram-notification
command containing the literal text "git push --tags" inside a message string (no git invocation
anywhere near it) was blocked. The same gap likely permits writing a mutating git command into a
script file and executing that file, since the hook only inspects the Bash tool's literal command
text, not files it goes on to execute.

- [ ] Read `~/cc/claude/enforce-git-branch-policy-hook-hardening.md` for the full analysis and
  ranked fix options.
- [ ] Decide whether to harden the text-matching (tokenize instead of substring-match; follow
  `bash <file>`/`sh <file>` into the target file's contents) or move enforcement into a real git
  `pre-push`/`pre-commit` hook (immune to every text-level bypass, since git invokes it directly
  regardless of how the git command was run) — the write-up recommends the latter as strictly more
  robust.
- [ ] This is a `claude-code-config-sync` change, not a CCST one — implement there, not here.

## Tidy up `catchup.py` and siblings' module path

`catchup.py`, `session_tag.py`, `after_response.py`, and `messaging_deliver.py` all live under
`src/cc_session_tools/cccs_hooks/` — a package name inherited from when these hooks were CCCS
(`claude-code-config-sync`) code, before they moved into this repo. The `cccs_hooks` name no
longer describes anything: these are CCST hooks now, dispatched via `HOOK_VERBS` in
`lib/hook_registry.py` and invoked as `ccst hooks run <name>`.

- [ ] Rename the `cccs_hooks` package to something that reflects current ownership (e.g.
  `hooks`, matching how `lib/hook_registry.py` already refers to them).
- [ ] Update every import site and the `HOOK_VERBS` dispatch table in `lib/hook_registry.py`
  accordingly.
- [ ] Check for any other stale `cccs_hooks`-shaped naming elsewhere in this repo picked up by
  the same historical move (e.g. docstrings, test paths).
