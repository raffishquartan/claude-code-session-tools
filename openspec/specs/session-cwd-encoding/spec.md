# session-cwd-encoding Specification

## Purpose

Defines the single correct algorithm for encoding an absolute working-directory path into the
directory name Claude Code uses under `~/.claude/projects/`, and guarantees every part of this
codebase that needs that encoding uses the same implementation.

## Requirements

### Requirement: One correct cwd-encoding algorithm
The system SHALL provide exactly one function that encodes an absolute cwd into a
`~/.claude/projects/<encoded>/` directory name, matching Claude Code's own encoding: replace every
`/` with `-`, and every `.` with `-`.

#### Scenario: Path with no dots
- **WHEN** encoding `/Users/alice/repos/foo`
- **THEN** the result is `-Users-alice-repos-foo`

#### Scenario: Path containing a dotted username
- **WHEN** encoding `/Users/jane.doe/repos/foo`
- **THEN** the result is `-Users-jane-doe-repos-foo` (the dot in the username is replaced,
  not left as a literal `.`)

### Requirement: Every call site uses the shared encoding
Every place in this codebase that needs to locate or compute a `~/.claude/projects/<encoded>/`
directory name from an absolute cwd SHALL use the single shared encoding function, not an
independent reimplementation.

#### Scenario: Transcript lookup finds a dotted-cwd session
- **WHEN** the parent session's transcript lookup (used by the 8-digit-offer detector and the
  WORKLOG guard) resolves a transcript path for a cwd containing a dot
- **THEN** it locates the correct `~/.claude/projects/<encoded>/<session_id>.jsonl` file, using
  the same encoding Claude Code itself used to create that directory

#### Scenario: `move-session` computes a dotted-cwd project key correctly
- **WHEN** the `move-session` skill computes the source or destination `~/.claude/projects/`
  directory key for a cwd containing a dot
- **THEN** the computed key matches Claude Code's own encoding of that cwd (dots replaced, not
  left literal)
