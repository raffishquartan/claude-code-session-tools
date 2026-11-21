# cli/ccmsg-unarchive Specification

## Purpose
Lets you bring an archived message back to the live set when it was archived without anyone having handled it, so it is listed again at session start.

## Requirements

### Requirement: ccmsg unarchive restores archived, never-claimed messages
`ccmsg unarchive <id> [<id> ...]` SHALL restore each named archived message that was never claimed: to status `read` if it had been read, otherwise to `sent`. It SHALL report each id as restored or failed and exit non-zero when any id failed, while still processing the others.

#### Scenario: Archived read message is restored to read
- **WHEN** an archived message that had been read and never claimed is unarchived
- **THEN** its status is `read` and it is listed at the next session start for its recipient

#### Scenario: Archived never-read message is restored to sent
- **WHEN** an archived message that was never read is unarchived
- **THEN** its status is `sent`

#### Scenario: Claimed message is refused
- **WHEN** an archived message that had been claimed is unarchived
- **THEN** it stays archived, the id is reported as failed, and the exit status is non-zero

#### Scenario: Message that is not archived is refused
- **WHEN** a message in any status other than `archived` is unarchived
- **THEN** it is unchanged and the id is reported as failed

#### Scenario: Unknown id is reported without stopping the others
- **WHEN** one of several ids does not exist
- **THEN** that id is reported as failed, the others are restored, and the exit status is non-zero

### Requirement: A restored never-read message is delivered at the next session start
A message restored to `sent` that is addressed to a session or a project SHALL be delivered to that session, or to the next session of that project, at its next session start as a new message, even if that session's delivery cursor has already passed the message. It SHALL be marked read when delivered, so the sender's read receipt is produced.

#### Scenario: Restored never-read session message reaches a resumed recipient
- **WHEN** a never-read session-addressed message is archived, unarchived, and its recipient starts a session whose cursor is past the message
- **THEN** the digest lists it as a new message and it moves to `read`

#### Scenario: Restored never-read project message reaches a project session
- **WHEN** a never-read project-addressed message is unarchived and a session of that project starts with a cursor past it
- **THEN** that session's digest lists it as a new message
