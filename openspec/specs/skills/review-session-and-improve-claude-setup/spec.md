# skills/review-session-and-improve-claude-setup Specification

## Purpose
The interactive half of the self-improvement loop: reviews a session (or, periodically, the accumulated signals) for friction, keeps the cross-session backlog, applies only the changes the user approves, and records each review through CCST so loop health and the nudge see it.

## Requirements

### Requirement: The interactive review skill is bundled
CCST SHALL bundle a skill named `review-session-and-improve-claude-setup` under `src/cc_session_tools/skills/`, installed, uninstalled and checked by `ccst skills install`, `ccst skills uninstall` and `ccst doctor` like every other bundled skill. An existing non-symlink directory of that name in the skills target SHALL be left untouched: without `--force` the install SHALL report it and exit 1.

#### Scenario: Fresh install
- **WHEN** `ccst skills install --apply` runs against an empty skills directory
- **THEN** `review-session-and-improve-claude-setup` is a symlink to the bundled directory

#### Scenario: Copied directory already present
- **WHEN** the skills directory holds a real `review-session-and-improve-claude-setup` directory and `ccst skills install --apply` runs without `--force`
- **THEN** the directory and its files are unchanged, the command names it and exits 1, and `ccst doctor` reports `skill:review-session-and-improve-claude-setup` as FAIL

### Requirement: The skill reviews in three modes and changes nothing without approval
The skill SHALL describe Mode A (append one dated line to the backlog Inbox on salient friction), Mode B (full review of one session: gather signals, match backlog signatures, classify by lever, write the backlog, present a checkpoint table, apply only approved rows, report, record) and Mode C (periodic review of the accumulated signals, run on `/review-session-and-improve-claude-setup periodic` or a nudge). Mode A SHALL append only to an existing backlog with a `## Inbox` line and SHALL NOT create either, because the backlog is the user's opt-in to the loop. Every change to configuration SHALL wait for the user's approval of that item, and a `CLAUDE.md` change SHALL show the exact replacement text first.

#### Scenario: Periodic review with nothing actionable
- **WHEN** Mode C finds no actionable item
- **THEN** the skill reports no actionable findings, writes nothing to the backlog and still records the review

#### Scenario: Friction noted before the loop is set up
- **WHEN** Mode A would note friction and the backlog is missing or has no `## Inbox` line
- **THEN** the skill writes nothing, never creates the backlog or the heading, and mentions `ccst self-improvement init` in chat

### Requirement: The skill uses CCST commands for the loop's state
The skill SHALL read the loop state with `ccst self-improvement status` (including the headless cost since the last interactive review), SHALL record every completed Mode B or Mode C review with `ccst self-improvement record-review`, SHALL tell the user to run `ccst self-improvement init` when the loop is not set up, and SHALL NOT call any shell script or write `reviews.log` directly. It SHALL contain no personal names or machine-specific paths.

#### Scenario: Commands named by the skill exist
- **WHEN** the skill's text is checked
- **THEN** every `ccst self-improvement <verb>` it names is a verb of that command, and it names no `.sh` script
