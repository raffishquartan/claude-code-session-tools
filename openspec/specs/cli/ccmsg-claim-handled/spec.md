# cli/ccmsg-claim-handled Specification

## Purpose
Gives every message kind (session, project, description) one lifecycle in which `ccmsg claim` records that the message has been dealt with, so an auto-read message stays listed at session start until it is claimed.

## Requirements

### Requirement: Claim marks a message handled for every address kind
`ccmsg claim` SHALL accept a message in status `sent` or `read` and move it to `claimed`, whether it is addressed to a session, a project or a description. It SHALL fail with the already-claimed error for a message in status `claimed` or `archived`. Claiming is first-wins: when several sessions race to claim one message, exactly one succeeds.

#### Scenario: Auto-read session message is claimed
- **WHEN** a session-addressed message in status `read` is claimed
- **THEN** it moves to `claimed` with a claim time

#### Scenario: Auto-read project message is claimed by one session
- **WHEN** a project-addressed message in status `read` is claimed by one session of the project
- **THEN** it moves to `claimed` and a second claim attempt fails as already claimed

#### Scenario: Unsent description message is still claimable
- **WHEN** a `sent` description-addressed message is claimed
- **THEN** it moves to `claimed`

#### Scenario: Claimed or archived message cannot be claimed
- **WHEN** a message in status `claimed` or `archived` is claimed
- **THEN** the claim fails as already claimed and the message is unchanged

### Requirement: Claiming a read message keeps the original reader
Claiming a message that was already read SHALL NOT change who is recorded as having read it or when, so the sender's read receipt is unaffected.

#### Scenario: Reader stamp survives claim
- **WHEN** a message auto-read by one session is claimed by another
- **THEN** its recorded reader and read time are those of the auto-read

### Requirement: Archiving without claiming remains available
`ccmsg archive` SHALL continue to archive a message in any status without requiring or setting a claim. Such a message is not claimed, so it can be restored with `ccmsg unarchive`.

#### Scenario: Unclaimed read message discarded
- **WHEN** a `read` message is archived with `ccmsg archive`
- **THEN** it is archived with no claim time and `ccmsg unarchive` can restore it
