# cli/ccmsg-session-reference Specification

## Purpose
Lets a session address another session by name or uuid prefix, instead of only by full uuid, and
shows the sender's full uuid when a message is read, so a session known only from a message or a
directory name can still be reached.

## Requirements

### Requirement: `--to-session` accepts a uuid, a session name or a uuid prefix
`ccmsg send --to-session <ref>` SHALL accept a full session uuid, a session name, or a prefix of
at least 8 hex characters of a session uuid (hex digits and dashes). The message SHALL be stored
with the full session uuid as its recipient value, whichever form was given.

A canonical uuid (8-4-4-4-12 hex) SHALL be used as given without consulting the sessions store.
Any other value SHALL be tried first as an exact session name, then as a uuid prefix, against the
sessions store.

#### Scenario: Full uuid
- **WHEN** `--to-session` is a canonical uuid
- **THEN** the message is stored for that uuid, whether or not the sessions store knows it

#### Scenario: Session name
- **WHEN** `--to-session` is `20261005-example-tag` and the sessions store holds exactly one
  session with that name
- **THEN** the message is stored for that session's full uuid and `ccmsg list` shows
  `session=<full uuid>`

#### Scenario: Uuid prefix
- **WHEN** `--to-session` is the first 8 characters of a session uuid and exactly one stored
  session uuid starts with them
- **THEN** the message is stored for that session's full uuid

#### Scenario: A name that looks like a prefix
- **WHEN** a value is both a stored session name and the prefix of another session's uuid
- **THEN** the session with that exact name is used

### Requirement: An unresolvable or ambiguous reference is rejected before anything is stored
`ccmsg send` SHALL exit 2 with a message on stderr, and store nothing, when `--to-session`:
- is a name or prefix that matches no session in the sessions store;
- matches more than one session, in which case the message lists each candidate as short uuid,
  name and project directory so the sender can retry with a full uuid;
- is none of a uuid, a session name or a uuid prefix of at least 8 hex characters, in which case
  the message names the accepted forms.

#### Scenario: Unknown name
- **WHEN** `--to-session` is `20260101-no-such-session` and no stored session has that name
- **THEN** the command exits 2, prints that no session matches, and no message file is created

#### Scenario: Forked session
- **WHEN** two stored sessions share a name (a fork)
- **THEN** the command exits 2 and lists both candidates with their short uuids

#### Scenario: Prefix too short
- **WHEN** `--to-session` is `a80f`
- **THEN** the command exits 2 and names the accepted forms

#### Scenario: Placeholder text
- **WHEN** `--to-session` is `target-uuid`
- **THEN** the command exits 2 and names the accepted forms

### Requirement: `ccmsg read` shows the sender's full session uuid
`ccmsg read` SHALL print a `from_uuid:` line, directly after the `from:` line, holding the full
sender session uuid as stored with the message.

#### Scenario: Reading a message
- **WHEN** a message sent by session `a80f8695-1111-2222-3333-444444444444` is read
- **THEN** the output contains `from_uuid: a80f8695-1111-2222-3333-444444444444` after the `from:`
  line, and the other lines are unchanged
