---
name: compact-smart
description: Draft a compaction-preservation paragraph from the current session and hand back the exact /compact command to run - automates what the user otherwise asks for by hand ("give me a paragraph to pass to /compact"). Use when the user runs /compact-smart, or says "give me a paragraph to pass to compact", "draft a compact instruction", "help me compact this session", "what should I tell compact to keep", or is about to /compact and wants to be sure nothing important gets lost.
---

# compact-smart

Drafts the instructions `/compact [instructions]` should carry, then hands back the exact
command to run. Does **not** invoke `/compact` itself - Claude Code's own docs explicitly
exclude `/compact` from the set of built-in commands a skill can invoke, so the deliverable
here is a ready-to-run command, not a compaction event.

## Why this exists

Real usage (a week of transcripts across every project) shows the user asking, by hand, for
"a paragraph to pass to /compact" roughly 3 times for every bare `/compact` call - and
re-explaining what counts as "relevant" nearly every time. What already survives compaction
automatically (project-root `CLAUDE.md`, memory files, system/environment info) is re-loaded
fresh from disk; what does **not** reliably survive is anything that only ever existed in
the conversation itself - decisions, corrections, standing agreements, outstanding to-dos.
A `/compact` instruction protects those for the *next* compaction only; the resulting
summary is itself just conversation text again and can be trimmed by a *later* one - so this
skill also refreshes the session's `WORKLOG.md`, if it has one, as a second, more durable
safeguard.

## What to draft

Review the conversation so far (you already have it loaded - no need to re-read the
transcript file from disk) and produce a paragraph covering, in this order:

1. **Decisions and their rationale** - especially any point where the initial plan or
   approach was found wrong and corrected mid-session. State the corrected approach and WHY
   it changed, not just what the original plan was.
2. **Concrete artifact pointers** - file paths, PR numbers, branch names, commit hashes,
   OpenSpec change names, or anything else someone would need to go find the actual work.
3. **Standing constraints or policies** established or confirmed during this session that
   must carry forward (e.g. "no attribution lines in commits", "confirm before opening a
   PR", a data-handling rule agreed mid-session). These are easy for a generic summary to
   drop silently because they're usually stated once, early, and never repeated.
4. **Outstanding tasks, split into two distinct groups**: what you (Claude) will do next,
   and - separately and explicitly labeled - anything that is the *user's own* action to
   take (e.g. "you still need to delete branch X yourself", "review the PR before merging").
   Do not fold these two categories together.
5. **An explicit "what happens next" statement.** Never leave this implicit.

Keep it dense and concrete - file paths and specific facts, not vague summary language.

## Handling `working/WORKLOG.md`

Check whether the current session has one: `$CLD_SESSION_DIR/working/WORKLOG.md`.

- **If it exists**: update it with the same substance as the draft above (append or rewrite
  its "current state" section - match whatever structure the file already uses). This
  repo's own `worklog-guard` PreCompact hook blocks a manual `/compact` if this file is more
  than an hour stale, so refreshing it here also clears that gate for the command you're
  about to hand back.
- **If it does not exist**: do not create one. `ccd`/`ccr` never create a WORKLOG.md
  automatically - it is opt-in scratch content the user or Claude may choose to keep during
  a session, not mandatory state. Creating one here for the first time would be the skill
  inventing a convention that has never applied to this session.

## Confirmation step

Show the drafted paragraph to the user before finalizing anything. Do not skip this even if
the draft feels obviously complete - the point is to catch a miss before compaction discards
the detail, not after. Accept:

- Approval as-is, or
- Free-form edits, or
- An explicit exclusion ("drop the X thread, keep everything else") - a stale, unrelated
  tangent that happened to occur in the same session is a real, recurring case; when asked,
  remove that content from the paragraph rather than trying to talk the user out of it.

This is a single review-and-edit round, not a multi-question interview - draft first, then
let the user correct it in one pass. Asking a series of clarifying questions before drafting
anything reintroduces exactly the friction this skill exists to remove.

## Final output

Once confirmed, present the exact command on its own line, ready to copy or type verbatim:

```
/compact <finalized paragraph>
```

Do not summarize what you're about to do instead of showing the literal command - the
command itself is the deliverable.
