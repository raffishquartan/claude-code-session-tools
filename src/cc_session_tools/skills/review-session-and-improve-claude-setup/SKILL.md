---
name: review-session-and-improve-claude-setup
description: Use when invoked via /review-session-and-improve-claude-setup (add "periodic" for the periodic review), when a session-start line says "Self-improvement review due", or the user asks "review this session for improvements" / "what could be better about my setup", or when Claude notices friction mid-session: denied permission, user correction or redo, retry loop, manual workaround for something a skill already handles, or a missed existing skill. Use proactively on friction without waiting to be asked.
---

# Review Session and Improve Claude Setup

## Overview

Reviews a Claude Code session for friction and inefficiency, then proposes
concrete improvements to the Claude setup - global or project `CLAUDE.md`,
skills, hooks, permissions, `settings.json`, and MCP/plugin configuration -
so future sessions go better.

Findings are written to a persistent cross-session backlog at
`~/.claude/self-improvement-backlog.md` and classified by actionable lever.
Nothing is changed without an explicit user checkpoint.

Goal behind the goal: the Claude setup should move toward self-improvement.
*Noticing* that things could be better should need as little prompting as
possible; *changing* anything always passes a human checkpoint.

Noticing is partly automated by CCST's self-improvement loop (see "Automated
loop" below): a weekly signal collector, a weekly headless review that
proposes Inbox lines, and a SessionStart nudge that asks for a periodic
review (Mode C) when the signals warrant one. The automated parts only
propose. Deciding and applying stay in an interactive Mode B or Mode C
review.

**The loop must be set up first.** It counts as set up once the backlog
exists with a `## Inbox` line. If `ccst self-improvement status` reports
`loop: not set up`, tell the user to run `ccst self-improvement init` (it
creates the backlog and never overwrites one) and stop until they have.

## When to Use

**Use when:**
- Invoked explicitly: `/review-session-and-improve-claude-setup`
- Periodic review (Mode C): `/review-session-and-improve-claude-setup periodic`,
  or a session-start line reading "Self-improvement review due: ..."
- The user asks "review this session", "what could be improved", "what went wrong"
- Claude hits salient friction mid-session (see Mode A below) - use
  proactively without waiting to be asked
- End of any substantive session where friction was observed

**Do NOT use for:**
- Finding problems in a clean session - when there is no friction, output
  "no actionable findings", write nothing to the backlog and still record the
  review (Mode B Step 9)
- Pure read/exploration sessions that produced no state changes
- Static token-footprint / context-size optimisation - that is owned by the
  `reduce-persistent-context` skill
- Token and dollar accounting - that is owned by `analyse-cc-usage`

---

## Mode A - Inline Friction Note (cheap, best-effort)

When friction is salient mid-session - a denied permission, a user
correction or redo, a retry loop, a manual workaround for something an
existing skill already handles, or a missed existing skill - append ONE
dated line to the backlog Inbox. Do not run a full analysis; do not
interrupt the session beyond the single append.

**Format:**
```
- [yyyy-MM-dd] [session-tag] <one-line observation>
```

Get the date with: `date -u +%Y-%m-%d` (the loop ages Inbox lines in UTC days).

**Location:** `~/.claude/self-improvement-backlog.md` -> `## Inbox` section.
Append below any existing Inbox lines (newest at the bottom of the section).

**Only when the loop is set up.** Append only if the backlog exists and has a
line that is exactly `## Inbox`. Never create the backlog or add that heading:
it is the user's opt-in to the loop, which starts the weekly headless review
(subscription usage) and the nudge. If it is missing, mention the friction in
chat and that `ccst self-improvement init` would start recording such notes.

> The backlog may be a symlink (for example into a repository that keeps the
> Claude configuration under version control), and the Edit tool refuses to
> write through symlinks. Resolve the real path first with
> `readlink -f ~/.claude/self-improvement-backlog.md` and Read/Edit that
> resolved path.

**Coverage limitation:** Mode A is best-effort: Claude logs when friction is
salient. The weekly headless review backfills friction nobody noted by
scanning recent transcripts and appending `[auto-review]` Inbox lines, but it
runs at most once a week over a capped digest. Reliable capture of every
friction event as it happens would need a Stop hook, which does not exist yet.

---

## Mode B - Full Review Pipeline

Run on explicit invocation, or proactively when warranted by the session.

### Step 1: Resolve session

Identify the current session UUID. Locate the facet JSON at:
`~/.claude/usage-data/facets/<uuid>.json`

Relevant keys if the file exists: `friction_detail`, `friction_counts`,
`outcome`, `user_satisfaction_counts`, `brief_summary`, `session_id`.

If the file does not exist (common - facets are written after session end):
proceed with transcript-only review; do not fail or report an error.

### Step 2: Gather hybrid signals

Combine all available signal sources:
- **Facet JSON** (`friction_detail`, `outcome`, `user_satisfaction_counts`,
  `brief_summary`) when present.
- **Live transcript / working memory:** retries, denied permissions, user
  corrections and redos, manual work that a tool or skill could have handled,
  inefficient large file reads, missed existing skills that would have applied.
- **Backlog Inbox notes** accrued during this session (from Mode A).

### Step 3: Match against existing backlog signatures

For each finding, read the `## Entries` section of
`~/.claude/self-improvement-backlog.md` and compare the normalised phrase
against each entry's `signature` field.

- **RECURRENCE:** signature matches an existing entry -> bump `recurrence` by
  1, append the current session tag to `sessions`, do not create a duplicate.
- **NEW:** no signature match -> prepare a new entry (written in Step 5).

### Step 4: Classify by lever

Assign each finding to exactly one lever from the taxonomy below. Drop
one-offs, environmental issues, and user-error noise as `none` - these must
not inflate the backlog with un-actionable items.

#### Lever taxonomy

| Lever | Example friction -> proposal |
|---|---|
| `claude-md` | Repeated user correction of a formatting or behaviour rule -> add or adjust a rule in global or project `CLAUDE.md` |
| `skill` | Manual multi-step workaround with no existing skill -> propose a new skill; or a skill triggered late -> tune its description |
| `hook` | A recurring class of mistake the harness could catch -> propose a hook (design only) |
| `permission` | Repeated permission prompt for a safe command -> propose a `settings.json` allow rule |
| `settings` | A config default that caused friction -> propose a settings change |
| `mcp-plugin` | A task needed a tool that does not exist, or a broken server -> propose an MCP/plugin gap or fix |
| `none` | One-off, environmental, or user-error noise -> record nothing actionable; drop silently |

### Step 5: Write / update backlog

Update `~/.claude/self-improvement-backlog.md` (resolve a symlink first, as
in Mode A):

- **New entries:** append to `## Entries` with status `open` and all fields
  populated (see the entry schema below).
- **Recurrences:** edit the matching entry in place - increment `recurrence`,
  append the session tag to `sessions`. Never create a duplicate.
- **Inbox consolidation:** remove any Inbox lines from this session that have
  been incorporated into Entries. Leave lines from other sessions that have
  not yet been reviewed.

**Entry schema:**
```
### [B-NNN] <short title>
- signature: <normalised phrase for recurrence matching>
- lever: claude-md | skill | hook | permission | settings | mcp-plugin | none
- status: open | proposed | approved | applied | dropped
- first-seen: yyyy-MM-dd
- recurrence: <int>
- sessions: <tag>, <tag>, ...
- friction: <what went wrong, observed facts>
- proposal: <concrete change, naming the exact file or lever>
- confidence: low | med | high
```

Keep `- status: <status>` on its own line exactly as shown: the signal
collector counts `open` and `proposed` entries from those lines.

### Step 6: Present checkpoint table

Surface items that are worth acting on now. Population rule: include any
entry with **recurrence >= 2** OR **confidence = high**.

Table columns:

| id | lever | target file / area | proposed change | confidence | recurrence |
|---|---|---|---|---|---|

Wait for the user to approve, reject, or defer each row before proceeding.

**Consolidation worked example:**
Three Inbox lines - `permission prompt for ripgrep`, `had to approve rg again`,
`rg blocked` - all share the signature `repeated-permission-prompt:ripgrep`.
They consolidate into ONE `permission`-lever entry with `recurrence: 3`, not
three separate entries.

### Step 7: Apply approved changes

For each row the user approves:
- Make surgical edits to the named lever (file, settings key, etc.).
- Mark the entry `applied` in the backlog.
- If an approved change fails to apply (e.g. the target has moved), report
  the failure plainly and leave the entry `approved` (not `applied`). Do not
  partially apply.

### Step 8: Report inline

Summarise in chat:
- What was found and logged to the backlog.
- What was applied.
- What remains open or deferred.

Show or summarise all changed artefacts inline. Never hand back a bare
`file://` path - paste the relevant diff or summary directly in the response.

### Step 9: Record the review

Every completed Mode B review records exactly one line in the loop's review
log, including a review with no actionable findings. This log is the loop's
usage metric and the source of "days since last review":

```
ccst self-improvement record-review --mode B --findings <findings> --applied <applied> --deferred <deferred>
```

- `findings`: findings logged to the backlog (new entries plus recurrences).
- `applied`: approved changes applied in Step 7.
- `deferred`: rows deferred or rejected at the Step 6 checkpoint.

Never write the review log by hand: `record-review` writes the exact format
the loop reads, in the state directory CCST uses on this install.

---

## Mode C - Periodic Review

Run on `/review-session-and-improve-claude-setup periodic`, or when the
SessionStart nudge says "Self-improvement review due". It reviews the
accumulated signals rather than one session.

1. **Snapshot.** Run `ccst self-improvement status`. It prints whether the
   loop is set up, the latest counts-only snapshot, the last interactive
   review, the last headless run and the headless cost since the last
   interactive review. If the loop is not set up, tell the user to run
   `ccst self-improvement init` and stop. If the snapshot is `none`, say so:
   the `self-improvement-signals` job has not run yet (`ccsched status
   self-improvement-signals`); `ccst self-improvement collect-signals
   --no-escalate` refreshes it on demand.
2. **Backlog.** Read the `## Inbox` lines and every entry with status `open`
   or `proposed` (resolve a symlink first, as in Mode A). Treat
   `[auto-review]` lines like any other Inbox line: consolidate each into an
   entry (new or recurrence) or drop it as `none`.
3. **Messages.** `ccmsg list` and pick the unclaimed messages about the
   Claude configuration itself (skills, hooks, `CLAUDE.md`, settings).
   Subjects may be read in this session but are never written to any state
   file or the backlog. Messages about other projects are counted only; they
   are handled in those projects.
4. **Reliability logs.** Read the markdown table rows with a cell starting
   "Open - none fixed" in `~/.claude/skills/*/references/*.md`.
5. **Hook denies.** `ccst telemetry query --decision deny --since 14d --limit 500`
   and look for repeated blocks of the same kind.
6. **Classify and decide.** Run Steps 3-7 of Mode B unchanged on these
   findings, so every item still goes through the Step 6 checkpoint table. A
   `claude-md` lever item must show the exact replacement text and wait for
   the user's explicit confirmation before anything is written.
7. **Headless cost.** Report the cost of the headless runs since the last
   interactive review: the `headless cost since last interactive review`
   line of `ccst self-improvement status` (attempts, known USD, attempts with
   unknown cost).
8. **Report** as in Mode B Step 8, then **record** the review as in Step 9
   but with `--mode C`:

   ```
   ccst self-improvement record-review --mode C --findings <findings> --applied <applied> --deferred <deferred>
   ```

   Record it even when nothing was actionable.

---

## Automated loop

The automated parts only propose; none of them edits configuration. All are
part of CCST.

| Part | Where | What it does |
|---|---|---|
| Signal collector | `ccst self-improvement collect-signals`, bundled ccsched job `self-improvement-signals`, every 7 days | Counts Inbox lines and age, open/proposed entries, ccmsg messages unclaimed for 14+ days, hook denies in 7 days, the drift push count, "Open - none fixed" reliability rows. Writes the snapshot, prints one line with loop health and auto-review health, and on a FAIL sends one Telegram message (at most one per 7 days). No model call. |
| Headless review | `ccst self-improvement auto-review`, bundled ccsched job `self-improvement-auto-review`, every 7 days | One `claude -p` call (Sonnet, no tools, `--max-budget-usd 0.50`, subscription login only) over a digest of at most 120 KB; appends at most 10 validated `[auto-review]` lines to the Inbox; logs its cost; Telegram on failure or overspend. Skipped when there are no signals and no new sessions, and at most one paying run per 7 days. |
| Nudge | SessionStart hook `self-improvement-nudge` | At session start, at most once a day: due when the last interactive review is 14+ days old and a signal is non-zero, or the signal total is 10+, or the last review is 30+ days old (or none is recorded). |

Until the loop is set up (`ccst self-improvement init`), the jobs make no
model call and send nothing, and the nudge is silent.

Only Mode B and Mode C reviews (recorded with `record-review`) count as
reviews. Headless runs never reset "days since last review": the headless
review proposes, it does not decide. Loop health is PASS when the last
interactive review is at most 21 days old and the oldest Inbox line at most
30 days old; auto-review health is PASS when the headless review ran or
skipped within the last 8 days.

`ccst self-improvement nudge` previews whether a review is due without
using up the day's nudge.

---

## Fallback paths

**Missing facet JSON:** Proceed with transcript-only review (Steps 2-8
unchanged). Omit the facet signal from Step 2. Do not report an error.

**No real friction found:** Output "no actionable findings". Write nothing
to the backlog, but still record the review (Step 9) with `--findings 0
--applied 0 --deferred 0`. Do not invent problems to look productive.

---

## Guardrails

1. **Approval before any change.** Every lever - `claude-md`, `skill`, `hook`,
   `permission`, `settings`, `mcp-plugin` - is a CHECKPOINT. Nothing is
   applied without the user approving the specific item in the Step 6 table.
   For a `claude-md` item (any `CLAUDE.md`, or a file one imports), show the
   exact replacement text and write nothing until the user explicitly
   confirms it. The collector, the headless review and the nudge never apply
   anything.

2. **No personal identifiers in shared files.** Never write personal
   identifiers (real names, email addresses, phone numbers, postal addresses)
   into any committed file or skill without the user's explicit approval for
   that content.

3. **No auto-commit.** The backlog and skill files may be written locally.
   Git commits require explicit user approval.

4. **No invented findings.** If the session had no real friction, say so.
   Do not manufacture findings or pad the backlog with un-actionable items.

5. **Artefacts inline.** Show changed content or a clear summary in chat.
   Never return a bare `file://` path as the sole output.

6. **No Stop hook.** Proactive Mode A noticing is best-effort, and the
   weekly headless review only backfills from a capped digest.

7. **Always record.** Every completed Mode B or Mode C review runs
   `ccst self-improvement record-review`, even with nothing actionable.

---

## Common Mistakes / Red Flags

- About to apply a `CLAUDE.md` edit without showing the diff - **STOP**, present the checkpoint table first
- About to write a backlog entry for a one-off environmental issue - **DROP it as `none`**
- About to create a second backlog entry for a signature that already exists - **STOP**, bump `recurrence` instead
- Manufacturing findings on a clean session - **STOP**, output "no actionable findings", write nothing to the backlog, and still run `record-review`
- Handing back a bare `file://` path instead of inline content - **STOP**, summarise the artefact in chat
- Clearing the Inbox before consolidating the lines into Entries - **STOP**, consolidate first, then clear
- Reporting wrap-up complete without having checked the backlog for Inbox lines from this session - **STOP**, run Step 2 first
- Writing the review log by hand instead of `ccst self-improvement record-review` - **STOP**, use the command
- About to commit the backlog or skill to git without approval - **STOP**, present the commit for user sign-off
