## Purpose

Lets you bring an archived message back to the live set when it was archived without anyone having handled it, so it is listed again at session start.

## ADDED Requirements

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
