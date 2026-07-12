# CCST global CLAUDE.md bootstrap prompt

This file is a self-contained prompt intended to be run via:

```sh
cd ~/repos/claude-code-session-tools && \
  claude -p "Check that you are executing with the claude-code-session-tools repository as your cwd. If you are not then exit. If you are then use this file as your prompt: docs/global-claude-md-bootstrap-prompt.md"
```

---

## Step 1 — Verify repository context

Before doing anything else, check that the current working directory is the
`claude-code-session-tools` repository.

Run:
```sh
pwd && ls pyproject.toml src/cc_session_tools 2>/dev/null && echo "CCST repo confirmed"
```

If `pyproject.toml` and `src/cc_session_tools` are both present, proceed.
If not, print the following message and exit immediately:

> ERROR: This prompt must be run from the claude-code-session-tools repository
> root. Expected to find pyproject.toml and src/cc_session_tools/ in the
> current directory. Aborting without touching ~/.claude/CLAUDE.md.

---

## Step 2 — Read the user's current global CLAUDE.md

Read `~/.claude/CLAUDE.md`. If the file does not exist, note that it will be
created from scratch. If it exists, read its full contents so you can write
idempotently.

Note any existing sections so you do not duplicate them.

---

## Step 3 — Detect installed CCST components

Run the following checks and collect their results into a summary table:

```sh
# CLIs
ccd --version 2>&1
ccr --version 2>&1
ccs --version 2>&1
ccst --version 2>&1
claude-code-usage --version 2>&1

# ccl shell function
grep -s "ccl()" ~/.bashrc ~/.zshrc 2>/dev/null && echo "ccl: installed" || echo "ccl: NOT installed"

# Skill symlinks
ls -la ~/.claude/skills/find-claude-code-session 2>/dev/null && echo "skill: find-claude-code-session OK" || echo "skill: find-claude-code-session MISSING"
ls -la ~/.claude/skills/move-session 2>/dev/null && echo "skill: move-session OK" || echo "skill: move-session MISSING"
ls -la ~/.claude/skills/analyse-cc-usage 2>/dev/null && echo "skill: analyse-cc-usage OK" || echo "skill: analyse-cc-usage MISSING"
ls -la ~/.claude/skills/list-empty-sessions 2>/dev/null && echo "skill: list-empty-sessions OK" || echo "skill: list-empty-sessions MISSING"
ls -la ~/.claude/skills/delete-sessions 2>/dev/null && echo "skill: delete-sessions OK" || echo "skill: delete-sessions MISSING"
ls -la ~/.claude/skills/generate-8digit-code 2>/dev/null && echo "skill: generate-8digit-code OK" || echo "skill: generate-8digit-code MISSING"

# Hook registrations (check for ccst hooks run entries)
python3 -c "
import json, pathlib
p = pathlib.Path.home() / '.claude/settings.json'
if not p.exists():
    print('settings.json: MISSING')
else:
    data = json.loads(p.read_text())
    hooks = data.get('hooks', {})
    cmds = [h['command'] for evt in hooks.values() for group in evt for h in group.get('hooks', []) if 'ccst hooks run' in h.get('command', '')]
    print(f'hooks registered: {len(cmds)}')
    for c in cmds:
        print(f'  - {c}')
" 2>/dev/null || echo "Could not parse settings.json"
```

Print the results in a clear summary. Mention any missing components and what
the user can do to fix them (e.g., `ccst skills install --apply`,
`ccst hooks install --apply`, `ccst shell install --apply`).

---

## Step 4 — Propose standard CLAUDE.md additions

Present the following proposed additions to the user's global CLAUDE.md:

```
<!-- ccst-bootstrap: start -->
## claude-code-session-tools

The following CLIs are available in this environment for session management
and usage analytics. Prefer them over starting new Claude Code sessions
inside a running one.

### Session management

- Use `ccs` (or `ccl`) to list all sessions in the current project,
  newest-first. `ccl` is a shell function wrapping `ccs --global` for
  convenience — activate it with `source ~/.bashrc` if needed.
- Use `ccr <fragment>` to resume a session by any substring of its name.
- Use `ccd <tag>` to start a new session with a tagged, dated directory
  under `cc-sessions/`.
- **Do not start new Claude Code sessions from inside a running session.**
  Use the shell CLIs above from your terminal instead.

### Bundled skills

When the user asks about prior sessions, invoke the **`find-claude-code-session`**
skill (it wraps `ccs` with escalating scope).

When the user wants to move or rename a session, invoke the **`move-session`** skill.

When the user wants to clean up never-used sessions (accumulated from accidental
`ccd` invocations or abandoned starts), invoke **`list-empty-sessions`** to
identify them, then **`delete-sessions`** to remove them (requires 8-digit
confirmation).

When the user asks about Claude Code usage, token spend, or cost breakdowns,
invoke the **`analyse-cc-usage`** skill.

### 8-digit confirmation codes

**Always use the `generate-8digit-code` skill when you need a confirmation
code for a gated action. Never invent or guess a number yourself.**

LLMs are not random number generators. A model-generated number is
predictable and statistically biased — it defeats the purpose of
the confirmation gate. The skill runs `scripts/generate_8digit_code.py`
which uses Python's `secrets` module (cryptographically secure).

When proposing a gated action, run the skill, then say:
> "Respond with `NNNNNNNN` to confirm."

Only proceed once the user replies with exactly that string.

### 8-digit gated actions

Certain high-stakes actions require the user to type an 8-digit confirmation
code before Claude proceeds. The hook `ccst hooks run confirm-8digit` enforces
this. The list of gated action classes is below.

> NOTE: The `notify-user` skill (tracked in TODO.md of the ccst repo) is a
> recommended companion: when installed and configured, it sends a push
> notification when a gate fires so you don't have to be at the terminal.

**Gated action classes (user-configured below):**

[PLACEHOLDER — replaced by Step 5 output]
<!-- ccst-bootstrap: end -->
```

Tell the user you will now ask them which action classes they want gated.

---

## Step 5 — Interactive: choose 8-digit gated action classes

**ASK USER:**

Explain that the 8-digit confirmation hook (`ccst hooks run confirm-8digit`)
blocks execution and waits for the user to type a random 8-digit code before
Claude proceeds. This prevents accidental irreversible actions during long-running
or background agent tasks.

Propose the following default gated action classes, and ask the user to
confirm, remove, or add to the list:

**Proposed defaults:**
1. Pushing commits to a remote repository (`git push`)
2. Force-pushing to any branch (`git push --force` / `git push -f`)
3. Merging or landing a pull request
4. Deleting a local or remote branch (`git branch -D`, `git push origin --delete`)
5. Financial transactions or purchases (Tesco orders, PayPal, Stripe, etc.)
6. Sending external messages (email, WhatsApp, Telegram, Slack to real people)
7. Deleting files or directories with `rm -rf` on paths outside the current repo
8. Running `DELETE` / `DROP` SQL statements against a real (non-test) database

Present the list and ask:
- "Which of these do you want to keep? (type the numbers, e.g. 1 2 3 4)"
- "Any additional classes you want to add? (describe them in plain English)"

Wait for the user's response before proceeding to Step 6.

---

## Step 6 — Interactive: notify-user skill awareness

**ASK USER:**

Briefly explain the `notify-user` companion skill:

> The 8-digit confirmation gate blocks until you type a code at the terminal.
> For long-running agents or background tasks, you may be away from the
> keyboard when a gate fires — meaning the agent silently stalls until you
> return.
>
> A separate `notify-user` skill (tracked in TODO.md of the
> claude-code-session-tools repo) can send a push notification (Telegram,
> ntfy, Pushover, or similar) whenever a gate fires, so you can confirm from
> your phone. The skill is not yet publicly released; see TODO.md for current
> status.

Ask: "Do you want a reminder about this in your CLAUDE.md? [y/N]"

Note their preference; you will include or omit the notify-user paragraph in
the final write accordingly.

---

## Step 7 — Write the additions to ~/.claude/CLAUDE.md

Using the user's answers from Steps 5 and 6, construct the final block to
write between the sentinel markers:

```
<!-- ccst-bootstrap: start -->
## claude-code-session-tools
...
### 8-digit gated actions
- <class 1 the user chose>
- <class 2 the user chose>
- ...
[optional: paragraph about notify-user if user said yes]
<!-- ccst-bootstrap: end -->
```

Write idempotently:
- If `~/.claude/CLAUDE.md` already contains `<!-- ccst-bootstrap: start -->` and
  `<!-- ccst-bootstrap: end -->` markers, **replace** the entire block between
  them (including the markers themselves) with the new block.
- If the markers are absent, **append** the new block at the end of the file
  (preceded by a blank line).

After writing, read back the written section and confirm to the user:
- The path written to.
- The number of lines in the new block.
- A brief summary of the gated action classes they chose.

---

## Step 8 — Final recommendations

After writing, tell the user:

1. Run `ccst doctor` to verify the full install is healthy.
2. If `ccl` was not detected in Step 3, run `ccst shell install --apply` and
   then `source ~/.bashrc` (or open a new shell) to activate it.
3. If any skills were missing, run `ccst skills install --apply`.
4. If hooks were not registered, run `ccst hooks install --apply`.
5. Telemetry is pruned automatically every 7 days via a `ccsched` job. If they
   want tighter one-off pruning in the meantime, use
   `ccst telemetry trim --max-age-days 30 --max-size 5`.

Remind them that re-running this bootstrap prompt at any time is safe — it
replaces the sentinel block rather than appending.
