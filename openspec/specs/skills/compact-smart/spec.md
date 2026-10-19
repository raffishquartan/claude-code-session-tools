# skills/compact-smart Specification

## Purpose

Automates drafting the compaction-preservation instructions Chris otherwise asks Claude for
by hand before most `/compact` calls, so decisions, pointers, and his own outstanding
to-dos reliably survive summarization without him re-explaining "relevant" each time.

## Requirements

### Requirement: Drafts a structured preservation paragraph from the current session
When invoked, the skill SHALL draft a compaction-preservation paragraph covering: decisions
made and their rationale (especially any plan that was corrected mid-session), pointers to
concrete artifacts (file paths, PR/branch/commit references), standing constraints or
policies established during the session that must carry forward, outstanding tasks -
distinguishing tasks Claude will do next from actions only the user can take - and an
explicit statement of what happens next.

#### Scenario: A session with a mid-course correction preserves the correction, not just the original plan
- **WHEN** the session changed its approach partway through (an initial plan was found wrong
  and replaced)
- **THEN** the draft states the corrected approach and the reason it changed, not only the
  original plan

#### Scenario: A user-only action is called out distinctly
- **WHEN** the session identified an action that only the user can perform (e.g. deleting a
  branch, running something manually)
- **THEN** the draft states it as a distinct outstanding item, not folded indistinguishably
  into Claude's own next steps

### Requirement: Never invokes compaction itself
The skill SHALL NOT attempt to trigger `/compact` on the user's behalf; it SHALL end by
presenting the exact `/compact <instructions>` command for the user to run.

#### Scenario: The skill's output is a ready command, not a compaction event
- **WHEN** the skill finishes drafting and the user has confirmed the content
- **THEN** the skill's final output names the exact command to run, and no compaction has
  occurred as a side effect of the skill itself

### Requirement: The user reviews and can adjust the draft before it is used
The skill SHALL show the drafted paragraph to the user and accept edits or exclusions before
finalizing the command to run - it SHALL NOT hand off a paragraph the user has not seen.

#### Scenario: The user excludes an unrelated thread
- **WHEN** the user asks for a topic or thread to be dropped from the draft (e.g. a stale,
  unrelated tangent that happened to occur in the same session)
- **THEN** the finalized paragraph does not reference that topic

#### Scenario: The user confirms without changes
- **WHEN** the user approves the draft as-is
- **THEN** the finalized command uses the draft unchanged

### Requirement: An existing session WORKLOG.md is refreshed, never created
If the current session already has a `working/WORKLOG.md` file, the skill SHALL update it
with the same substance as the drafted paragraph before finishing. If no such file exists,
the skill SHALL NOT create one.

#### Scenario: An existing WORKLOG.md becomes fresh enough to pass the staleness gate
- **WHEN** a session has a `working/WORKLOG.md` older than this repo's existing
  worklog-guard staleness threshold
- **THEN** after the skill runs, that file's content reflects the current session state and
  its modification time is recent enough that a subsequent manual `/compact` is not blocked
  by the worklog-guard hook

#### Scenario: A session with no WORKLOG.md is left without one
- **WHEN** the current session has no `working/WORKLOG.md`
- **THEN** the skill completes its draft-and-confirm flow without creating that file

### Requirement: The drafted paragraph is a single line of plain prose
The skill SHALL produce the compaction-preservation paragraph as one line of plain prose containing no line breaks, no bullets or numbered lists, no headings and no other markdown, because `/compact` accepts only a single string that the user pastes verbatim. The command shown for review and the final command SHALL both present the paragraph on one line.

#### Scenario: A long session with many decisions and tasks still yields one line
- **WHEN** the session contains many decisions, artifact pointers, constraints and outstanding tasks
- **THEN** the paragraph in the presented `/compact` command is a single line with no newline characters inside it, and Claude's tasks and the user's own tasks are separated by inline labels rather than list markup

#### Scenario: The reviewed draft is the pasted command
- **WHEN** the skill shows the draft to the user for confirmation
- **THEN** it is shown as the literal `/compact <paragraph>` command on a single line, and any edits the user requests keep it a single line
