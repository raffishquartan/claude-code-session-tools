---
name: pm-update-central-files
description: Use when the user is about to end a session or wants all coordination and record-keeping files brought up to date before exit. Triggers on "/pm-update-central-files", "wrap up the session", "update all the files", "before I exit", "bring everything up to date", "sync session state", or any prompt that signals pre-exit cleanup. Auto-updates items already mandated by global CLAUDE.md (WORKLOG.md, auto-memory, session-output index) without asking; presents a checkpoint table only for items that need human judgement (out/ deliverable renames, project CLAUDE.md edits, central coordination files, git commits).
---

# Update Central Files (pm-)

## Overview

Brings every file the session is responsible for up to date before the user exits. Invoked when the user wants to close the session cleanly without having to itemise each file type that needs updating.

The user runs multiple sessions that coordinate via shared files in a parent folder. Missing an update here means the next session starts with stale state. This is a `pm-` (project-management-family) skill: it manages per-project state, not this-project-only state — see `ccst pdata --help` for the shared per-project data store it now writes into (spec section 8, `2026-07-26-per-project-data-store-spec.md`).

## When to Use

- User invokes `/pm-update-central-files` directly
- User says "wrap up the session", "update all the files", "before I exit", "bring everything up to date"
- User signals the session is ending and updates have been deferred

Do NOT use for:
- Mid-session incremental saves - just write the specific file
- Fresh sessions that have not produced anything yet
- Sessions that were pure read/exploration with no state changes

## Two classes of items

**AUTO** items are already mandated by global CLAUDE.md (WORKLOG, memory rules) or are pure bookkeeping with no judgement call (the session-output index). Asking permission for them wastes the user's cycles since the answer is already required to be yes. Apply them immediately and report what was done.

**CHECKPOINT** items involve human judgement (file rename decisions, durable-fact gates, commit messages, per-file diff calls). Present these as a table and wait for approval.

### AUTO items

1. **WORKLOG.md** (`cc-sessions/<session>/working/WORKLOG.md`) - Rewrite in full reflecting the complete session history. NEVER ask permission - just do it. If it does not exist, create it.
2. **Auto-memory** - Apply CLAUDE.md memory rules: write durable, non-obvious items (user / feedback / project / reference); update or remove stale entries; keep `MEMORY.md` index in sync. NEVER write memory for ephemeral session content or things derivable from git.
3. **Session-output index** (`ccst pdata`, spec section 8) - for every file under this session's `cc-sessions/<session>/out/` that is not already registered, add it to the project's `session-output` index. `<project>` is the working directory's basename (the current project).

   First, ensure the extension schema (and its file_path index — see `ccst pdata
   reconcile-session-output`'s `--schema-only`) exists (idempotent - safe to re-run every time):
   ```
   ccst pdata reconcile-session-output --project <project> --schema-only
   ```
   Then, for each file `<name>` under `cc-sessions/<session>/out/`, check whether it is already registered:
   ```
   ccst pdata query --project <project> --group session-output --where "file_path = cc-sessions/<session>/out/<name>" --limit 1
   ```
   If that returns zero rows, register it:
   ```
   ccst pdata add --project <project> --group session-output --content "<one-line description, or the filename if none is obvious>" --file "cc-sessions/<session>/out/<name>" --field session_tag=<session>
   ```
   NEVER ask permission - like WORKLOG/memory, this is pure bookkeeping, not a judgement call (the only judgement involved - the one-line description - has a safe fallback: the filename). A 7-day `ccsched` job (`pm-session-output-reconcile`) backfills anything this step misses (a crashed or non-interactive session) - see `ccst pdata reconcile-session-output --help`. Do not overwrite an already-registered file's row; the reconciliation job's own semantics are also insert-if-missing, never update-on-every-run, so the two paths stay consistent.

### CHECKPOINT items

1. `cc-sessions/<session>/out/` deliverables that need version bumps:
    - File has a version suffix and content changed this session -> bump (e.g. `.v3.md` -> `.v4.md`). Surface in the table; safe to apply on approval.
    - File has NO version suffix and was updated -> ask whether to rename existing to `.v1.<ext>` before writing `.v2.<ext>`. NEVER rename without approval.
2. **Project `CLAUDE.md`** (working dir) - if a durable project-level fact was learned this session that warrants codifying. Do NOT add session narrative.
3. **Central coordination files** in the parent folder (INDEX.md, PEOPLE.md, STATUS.md, timelines, tracking logs) - per-file judgement. Read first, diff mentally, propose minimal updates.
4. **Git** - if the working directory is a git repo with uncommitted session changes, show `git status` and ask whether to commit. NEVER auto-commit (per global CLAUDE.md). Follow the one-branch-per-feature and small-coherent-commits rules.
5. **Correspondence audit** (conditional - see Step 1b) - only in projects where the working directory has a `correspondence/` folder and a project CLAUDE.md that mandates archiving. Surfaces any session-referenced messages not archived to `correspondence/`. Present as NEEDS ACTION (list each gap with "archive via archive-correspondence skill") or OK (no gaps). Never skip this row in qualifying projects.

## Process

### Step 1: Survey

Before doing anything, list:
- `cc-sessions/<session>/` contents (WORKLOG.md status, `out/`, `working/`)
- Working directory for a project `CLAUDE.md`
- Parent folder of working directory for central coordination files (files only, not just dir listing)
- `git status` if a repo
- `~/.claude/projects/<...>/memory/` to see what already exists
- `ccst pdata list --project <project> --group session-output` to see what the index already has for this project (informs Step 2's session-output AUTO item - only new files need adding)

### Step 1b: Correspondence audit (conditional)

Run this sub-step only if BOTH are true:
- The working directory contains a `correspondence/` folder
- The project CLAUDE.md exists and contains mandatory correspondence-archiving rules (look for the word "correspondence" near words like "mandatory", "MUST", or "archive")

If either condition is absent, mark correspondence audit as N/A in the Step 3 table and proceed.

If both conditions are met:

1. **Recall all correspondence references from this session.** Scan the session WORKLOG.md and recall tool calls from memory for any mention of specific messages retrieved, sent, or discussed - by ID, date, sender, subject, or platform (OFW, Gmail, WhatsApp, SMS, email). Look especially for:
   - Calls to platform MCP tools that return message content (`our-family-wizard_get_message`, `our-family-wizard_list_messages`, `gmail_read_message`, `whatsapp_list_messages`, or equivalents)
   - Messages sent via this session
   - WORKLOG entries describing correspondence as "outstanding", "not archived", "retrieved", "read", or "discussed"
   - Message IDs, dates, senders, or subjects mentioned in the context of fetching correspondence

2. **List `correspondence/`.** Run `ls <working-dir>/correspondence/` and note the filenames.

3. **Cross-check each reference.** The naming pattern is `<yyyy.MM.dd> <HHmm> - <sender> <channel> to <recipient>.<ext>`. A reference is covered if a `.md` file exists for that message (matching date, sender, and channel). A visual record (`.png` or `.pdf`) should also be present - note `.md`-only entries as a secondary gap.

4. **Compile the gap list.** Any referenced message with no matching `.md` in `correspondence/` is an unarchived gap. Messages flagged as "outstanding" in the WORKLOG count as gaps even if not individually identified in step 1.

5. **Add to the Step 3 CHECKPOINT table.** One row per unarchived message (or a single OK row if none). Proposed action for each gap: "Archive via archive-correspondence skill".

### Step 2: Apply AUTO items immediately

Write WORKLOG.md, write/update memory and `MEMORY.md` index, and register this session's new `out/` files into the session-output index. Do not ask - the global rules already mandate the first two, and the third is pure bookkeeping with a safe fallback (see AUTO item 3). In your response, briefly state what auto-applied (one line each), including a count of session-output rows added.

### Step 3: Present CHECKPOINT items as a table

Only the items that genuinely need user input. Columns: target, proposed verdict (UPDATE / SKIP / NEEDS INPUT), one-line detail. Wait for approval before writing anything in this list. If the table has zero rows, say so explicitly: "No checkpoint items - wrap-up complete after auto-applies."

### Step 4: Apply approved CHECKPOINT updates

Work through the approved list. Minimal, surgical edits.

### Step 5: Final report

Summarise: what auto-applied, what checkpoint-approved-and-applied, what skipped, what still open. Include file paths so the user can spot-check.

## Common Mistakes

- Asking permission for WORKLOG / memory / session-output-index updates - these are AUTO; the answer is already mandated
- Rewriting WORKLOG.md with only the latest chunk instead of the full session history
- Forgetting to bump version numbers on versioned deliverables
- Renaming an unversioned file to `.v1.<ext>` without asking first
- Adding session narrative to project CLAUDE.md (that is what WORKLOG and memory are for)
- Duplicating memory entries - check `MEMORY.md` for an existing one to update first
- Auto-committing to git without approval
- Skipping central coordination files because they "look old" - stable does not mean stale
- Treating `working/` files as deliverables requiring version bumps - they are scratch
- Skipping the correspondence audit in a project that mandates archiving - retrieved messages left unarchived will start the next session with stale state and may miss the gap-closing window
- Re-registering an already-indexed `out/` file (or updating its content) on every session-end run - the session-output index is insert-if-missing, never update-on-every-run; leave already-registered rows alone

## Red Flags

- About to ask "should I update WORKLOG.md" - STOP, just write it
- Asking "should I add a memory entry" before applying CLAUDE.md memory rules - STOP, apply the rules
- About to skip the session-output index step because "nothing in out/ looks important" - STOP, register every file, not just the ones that look significant
- About to apply a CHECKPOINT item without showing the user what's about to change - STOP, present the table
- Assuming no central coordination files exist without having listed the parent folder
- Creating a `.v2` without confirming the existing file is already `.v1` or needs renaming
- Writing memory entries that summarise this session rather than capture durable facts
- About to report wrap-up complete in a project with a `correspondence/` directory without having run the correspondence audit - STOP, check Step 1b first

## Quick Reference

| File type | Location | Class | Action |
|---|---|---|---|
| WORKLOG.md | `cc-sessions/<session>/working/` | AUTO | Rewrite in full, no ask |
| Memory + MEMORY.md | `~/.claude/projects/<...>/memory/` | AUTO | Apply CLAUDE.md memory rules, no ask |
| Session-output index | `ccst pdata` `session-output` group | AUTO | Register new `out/` files, no ask; never update already-registered rows |
| Versioned deliverable (`.vN.ext`) | `cc-sessions/<session>/out/` | CHECKPOINT | Bump version, surface in table |
| Unversioned deliverable | `cc-sessions/<session>/out/` | CHECKPOINT | Ask before renaming to `.v1.<ext>` |
| Scratch | `cc-sessions/<session>/working/` | AUTO | Overwrite in place |
| Project CLAUDE.md | Working dir | CHECKPOINT | Surface durable-fact change for approval |
| Central files | Parent folder | CHECKPOINT | Read, diff, propose minimal update |
| Git | Working dir repo | CHECKPOINT | Show status, ask for commit |
| Correspondence audit | `correspondence/` in working dir | CHECKPOINT (conditional) | Run Step 1b; surface gaps with "archive via archive-correspondence skill" |
