## Why

Chris repeatedly asks Claude, by hand, for "a paragraph to pass to /compact" before
compacting a session, to make sure decisions, file/PR pointers, standing constraints, and
his own outstanding to-dos survive the summarization. A week of real session transcripts
(all projects) shows this happening at roughly 3:1 instructed-vs-bare `/compact` calls, with
a consistent shape each time (see the research at
`cc-sessions/20260905-fork-disambiguation-impl-3-0-0/working/compact-smart-research.md` in
the `claude` project). A `/compact-smart` skill automates drafting that paragraph instead of
Chris re-asking and re-explaining "relevant" every time.

## What Changes

- New bundled skill `compact-smart`, invoked as `/compact-smart`. It drafts a compaction
  preservation paragraph from the current session, structured like Claude Code's own
  auto-generated pre-compact summary (decisions+rationale, file/PR/branch pointers,
  standing constraints, pending tasks split into "mine" vs. "yours", an explicit next step),
  refreshes the session's `working/WORKLOG.md` if one already exists (satisfying this repo's
  existing `worklog-guard` PreCompact hook), shows the draft to Chris for
  confirmation/edits/exclusions, then hands back the exact `/compact <text>` command to run.
- **Does not itself invoke `/compact`** - confirmed via Claude Code's own docs that
  `/compact` is explicitly excluded from the set of built-in commands a skill can invoke.
  The skill's deliverable is the ready-to-run command, not the compaction itself.

## Capabilities

### New Capabilities
- `skills/compact-smart`: the `/compact-smart` skill's behavior contract - what it drafts,
  what it does with an existing WORKLOG.md, and what it hands back to the user.

### Modified Capabilities
(none)

## Impact

- **Code**: new `src/cc_session_tools/skills/compact-smart/SKILL.md` (no backing script or
  CLI command - this is a pure instruction-guided skill, like `find-claude-code-session` or
  `reduce-persistent-context`, not a data-store-backed one like `context-override`).
  `README.md`'s bundled-skills table gains an entry.
- **No on-disk data format changes, no new CLI surface, no new data store** - minor version
  bump (new bundled skill), per this repo's own version policy.
