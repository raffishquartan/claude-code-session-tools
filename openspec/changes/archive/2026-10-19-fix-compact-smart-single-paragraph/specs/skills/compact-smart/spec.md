## ADDED Requirements

### Requirement: The drafted paragraph is a single line of plain prose
The skill SHALL produce the compaction-preservation paragraph as one line of plain prose containing no line breaks, no bullets or numbered lists, no headings and no other markdown, because `/compact` accepts only a single string that the user pastes verbatim. The command shown for review and the final command SHALL both present the paragraph on one line.

#### Scenario: A long session with many decisions and tasks still yields one line
- **WHEN** the session contains many decisions, artifact pointers, constraints and outstanding tasks
- **THEN** the paragraph in the presented `/compact` command is a single line with no newline characters inside it, and Claude's tasks and the user's own tasks are separated by inline labels rather than list markup

#### Scenario: The reviewed draft is the pasted command
- **WHEN** the skill shows the draft to the user for confirmation
- **THEN** it is shown as the literal `/compact <paragraph>` command on a single line, and any edits the user requests keep it a single line
