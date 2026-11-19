## Purpose

Ensures a message nobody has claimed is never silently dropped from view: automatic retention leaves it alone at any age and the session-start digest re-lists it with its age.

## ADDED Requirements

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
When a session starts, the delivery digest SHALL list every message in status `sent` that targets that session, whether or not an earlier digest already showed it, each with its relative age. The list SHALL be capped, and when more exist the digest SHALL state how many were omitted and point to `ccmsg list`.

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
