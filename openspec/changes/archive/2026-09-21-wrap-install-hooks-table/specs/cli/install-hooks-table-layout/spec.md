## Purpose

Defines how `ccst hooks install` and `ccst install-everything` present the bundled-hooks output, so
long hook descriptions stay readable at any terminal width instead of hard-wrapping into what
looks like unrelated rows.

## ADDED Requirements

### Requirement: Each hook renders as an indented multi-line record
The hooks install output SHALL render each hook as a record whose first line begins at column 0
with the hook name followed by its install status. Beneath it, indented, a line `Events: <events>`
and then the hook's description SHALL appear. The record SHALL NOT be preceded by a column-title
row or dashed rule. Consecutive records SHALL be separated by exactly one blank line, with no
trailing blank line after the last record.

#### Scenario: One hook
- **WHEN** the output is printed for a hook with a description
- **THEN** the first line starts at column 0 with the hook name and status, the next line is
  indented and starts with `Events:`, and the description follows on indented line(s)

#### Scenario: A hook with no description
- **WHEN** a hook has no registered description (for example a custom `--source` bundle whose
  command is not `ccst hooks run <name>`, where the hook's name is the whole command)
- **THEN** only the header and `Events:` lines are printed, with no empty description line

#### Scenario: Records are separated
- **WHEN** two or more hooks are printed
- **THEN** exactly one blank line separates consecutive records and none follows the last

### Requirement: The events and description lines wrap to the detected terminal width
The `Events:` line and the description block SHALL word-wrap to the effective width, so no line of
either exceeds that width (measured in characters), and every wrapped line carries the same
indent. The header line carries only the hook name and status and is not wrapped. A single token
longer than the available width is not split and MAY exceed the width. The effective width is the
width reported by `shutil.get_terminal_size()`, but never less than 40 columns.

#### Scenario: Narrow terminal
- **WHEN** the detected terminal width is 80 columns
- **THEN** no events or description line is longer than 80 characters

#### Scenario: Wide terminal
- **WHEN** the detected terminal width is 200 columns
- **THEN** descriptions wrap at the wider width, on fewer lines than at 80 columns

#### Scenario: Degenerate width
- **WHEN** the detected width is below 40 columns (for example `COLUMNS=20`)
- **THEN** the effective width is 40 columns

#### Scenario: No terminal
- **WHEN** the process's stdout is not a terminal and no `COLUMNS` environment variable is set
- **THEN** the output wraps at a fixed fallback width of 80 columns

#### Scenario: Paths and commands are not split
- **WHEN** a description contains a path, a backticked command or a hyphenated name
- **THEN** that token is never broken across lines

### Requirement: A hook registered for several events is listed once
A hook name registered for more than one event (or event/matcher pair) SHALL appear as one record
whose `Events:` line lists every event in bundle order, and whose status is `install` if any of
its registrations would be added, otherwise `already-installed`. When such a record has both new
and already-installed registrations, each event that would be added SHALL be suffixed `(new)`.
Records remain in alphabetical order by hook name.

#### Scenario: Multi-event hook, all installed
- **WHEN** a hook is registered for `SessionStart` and `UserPromptSubmit` and both are installed
- **THEN** there is exactly one record for it, its status is `already-installed`, its `Events:`
  line lists both events, and its description is printed once

#### Scenario: Multi-event hook, partly installed
- **WHEN** only its `UserPromptSubmit` registration is missing
- **THEN** its status is `install` and its `Events:` line marks `UserPromptSubmit` as `(new)` and
  `SessionStart` without a marker
