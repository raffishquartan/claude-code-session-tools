# cli/ccmsg-unread-resurface Specification

## Purpose
Ensures a message nobody has claimed is never silently dropped from view: automatic retention leaves it alone at any age and the session-start digest re-lists it with its age.

## Requirements

### Requirement: Automatic retention archives only claimed messages
Automatic retention SHALL archive only messages whose status is `claimed` and whose claim time is older than the retention window. A message in status `sent` or `read` SHALL NOT be archived automatically at any age, including a description-addressed message nobody has claimed.

#### Scenario: Old unread message survives retention
- **WHEN** a `sent` message addressed to a project is 60 days old and retention runs
- **THEN** it remains in status `sent`

#### Scenario: Old unclaimed description-addressed message survives retention
- **WHEN** a `sent` description-addressed message is 60 days old and retention runs
- **THEN** it remains in status `sent` and can still be claimed

#### Scenario: Old read message survives retention
- **WHEN** a `read` message was read 60 days ago and retention runs
- **THEN** it remains in status `read`

#### Scenario: Old claimed message is archived
- **WHEN** a `claimed` message was claimed more than the retention window ago and retention runs
- **THEN** it is archived

#### Scenario: Manual archive still works
- **WHEN** a message in any status is archived with `ccmsg archive`
- **THEN** it is archived

### Requirement: Session start re-lists pending messages with age
When a session starts, the delivery digest SHALL list every unclaimed description-addressed message (status `sent`) in that session's partitions, whether or not an earlier digest already showed it, each with its relative age. The list SHALL be capped, and when more exist the digest SHALL state how many were omitted and point to `ccmsg list`.

#### Scenario: Previously shown unclaimed proposal appears again
- **WHEN** a description-addressed proposal was shown to a session earlier, remains unclaimed, and a new session starts for a matching project
- **THEN** the new session's digest lists it with its age

#### Scenario: Claimed message is not re-listed
- **WHEN** a message has been claimed or read
- **THEN** the session-start digest does not list it as pending

#### Scenario: List is capped
- **WHEN** more pending messages exist than the cap
- **THEN** the digest lists the cap's worth and reports the count omitted

#### Scenario: Per-prompt delivery does not repeat the list
- **WHEN** a session receives an incremental delivery after session start with no new messages
- **THEN** the digest is empty

### Requirement: Session start lists read-but-unhandled messages until archived
When a session starts, the delivery digest SHALL also list every message addressed to that session or to its project that was auto-read but has not been archived, each with its relative age, in a section distinct from newly delivered messages. A message auto-read in the same delivery SHALL appear once, as newly delivered, not again in this section. The list SHALL be capped like the pending list. A message SHALL leave this section only when it is archived with `ccmsg archive`.

#### Scenario: Read message is listed at the next session start
- **WHEN** a session-addressed message was auto-read in an earlier session and is not archived
- **THEN** the next session start lists it as read but not yet handled, with its age

#### Scenario: Message read by another session of the project is listed
- **WHEN** a project-addressed message was auto-read by one session and a second session of that project starts
- **THEN** the second session's digest lists it as read but not yet handled

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
