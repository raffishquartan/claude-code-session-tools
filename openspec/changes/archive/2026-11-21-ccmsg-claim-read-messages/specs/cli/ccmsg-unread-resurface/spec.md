## RENAMED Requirements

- FROM: `### Requirement: Session start lists read-but-unhandled messages until archived`
- TO: `### Requirement: Session start lists read-but-unhandled messages until claimed`

## MODIFIED Requirements

### Requirement: Session start lists read-but-unhandled messages until claimed
When a session starts, the delivery digest SHALL also list every message addressed to that session or to its project that was auto-read but has not been claimed or archived, each with its relative age, in a section distinct from newly delivered messages. A message auto-read in the same delivery SHALL appear once, as newly delivered, not again in this section. The list SHALL be capped like the pending list. A message SHALL leave this section when it is claimed with `ccmsg claim` or archived with `ccmsg archive`. The section heading SHALL tell the session to run `ccmsg claim <id>` once the message is dealt with, and the digest SHALL print that command with the session's own `--uuid` and `--session` values filled in.

#### Scenario: Read message is listed at the next session start
- **WHEN** a session-addressed message was auto-read in an earlier session and is neither claimed nor archived
- **THEN** the next session start lists it as read but not yet handled, with its age

#### Scenario: Message read by another session of the project is listed
- **WHEN** a project-addressed message was auto-read by one session and a second session of that project starts
- **THEN** the second session's digest lists it as read but not yet handled

#### Scenario: Claimed message is not listed
- **WHEN** the message has been claimed with `ccmsg claim`
- **THEN** it no longer appears at session start, for any session of the project

#### Scenario: Archived message is not listed
- **WHEN** the message has been archived with `ccmsg archive`
- **THEN** it no longer appears at session start

#### Scenario: Message auto-read in this delivery is not duplicated
- **WHEN** a message is auto-read during the current session start
- **THEN** it appears exactly once, as newly delivered

#### Scenario: Another session's message is not listed
- **WHEN** a read message is addressed to a different session
- **THEN** it does not appear

#### Scenario: Per-prompt delivery does not repeat the list
- **WHEN** a session receives an incremental delivery after session start
- **THEN** the digest does not include this section

#### Scenario: Hint is runnable as printed
- **WHEN** the digest lists a read-but-unhandled message
- **THEN** the hint contains a `ccmsg claim <id> --uuid <session uuid> --session <label>` command with real values
