# sessions-uuid-fork-disambiguation Specification

## Purpose

Makes `sessions.db` uuid-aware so that a Ctrl-L fork of a session persists as its own
distinguishable record instead of silently overwriting its sibling, and defines the safe,
detectable, reversible path for upgrading an existing installation onto the new schema.

## Requirements

### Requirement: Forked sessions persist as distinct records
When two or more live sessions share the same project directory and tag (a Ctrl-L fork), the
system SHALL record each as its own distinct entry rather than one session's activity
overwriting another's.

#### Scenario: A second fork does not clobber the first fork's activity
- **WHEN** a session is forked (Ctrl-L) and both the original and the fork subsequently record
  activity (session open, a turn completing)
- **THEN** both sessions' recorded activity remains independently readable afterward - neither
  session's last-opened/last-active data is lost or overwritten by the other's

### Requirement: Session listings distinguish forks of the same tag
`ccl`/`ccs` and `ccst sessions list` SHALL list each fork of a tag as a separate entry, each
showing information (transcript size, last-active/last-opened time) specific to that fork rather
than information shared across all forks of the tag.

#### Scenario: Listing a forked tag shows both forks with independent details
- **WHEN** a tag has two live forks with different transcript sizes and different last-active
  times
- **THEN** the listing shows two entries for that tag, and each entry's size/timestamp reflects
  only that fork, not the other fork's or a value derived from files shared by both

#### Scenario: Listing an unforked tag is unchanged
- **WHEN** a tag has exactly one session associated with it
- **THEN** the listing shows exactly one entry for that tag, in the same format as before this
  capability existed

### Requirement: Resuming a forked tag disambiguates rather than guessing
When a user asks to resume a tag that has more than one live fork, the system SHALL present a
disambiguation choice rather than silently resuming an arbitrary one of the forks.

#### Scenario: Resuming an unambiguous tag needs no extra step
- **WHEN** a tag has exactly one associated session
- **THEN** resuming that tag resumes it directly, with no disambiguation prompt

#### Scenario: Resuming a forked tag prompts for which fork
- **WHEN** a tag has two or more live forks
- **THEN** resuming that tag presents the existing multi-match disambiguation choice, identifying
  each fork distinctly, instead of resuming one fork chosen arbitrarily

### Requirement: Moving or deleting a session acts on every fork of its tag together
Moving a session to a different project, or deleting a session, SHALL act on every fork sharing
that session's tag, since forks share the same underlying session directory and transcript
project folder.

#### Scenario: Deleting a forked tag removes all of its forks
- **WHEN** a user deletes a session whose tag has two live forks
- **THEN** both forks' records are removed together, not just the one the deletion targeted by
  coincidence

#### Scenario: Moving a forked tag preserves every fork at the destination
- **WHEN** a user moves a session whose tag has two live forks to a different project
- **THEN** both forks' records exist at the destination afterward, each still distinguishable by
  its own uuid, and neither is dropped or merged into the other

### Requirement: Upgrading to the new schema is detected accurately
`ccst doctor` SHALL report the schema migration's status accurately across every reachable state,
and SHALL NOT report a brand-new installation as needing migration.

#### Scenario: A pre-migration upgrade is flagged
- **WHEN** an existing installation's `sessions.db` still has the old (pre-fork-aware) schema
- **THEN** `ccst doctor` reports this migration as failing, naming the exact command to run to
  fix it

#### Scenario: A migrated installation passes
- **WHEN** `sessions.db` has already been migrated to the new schema
- **THEN** `ccst doctor` reports this migration as passing

#### Scenario: A brand-new installation is not flagged
- **WHEN** `sessions.db` did not exist before this version of the software was installed, and is
  created fresh by it
- **THEN** `ccst doctor` reports this migration as passing, not as failing - a fresh install has
  nothing to migrate

### Requirement: The schema migration is safe to run against a live installation
The migration SHALL take a restorable backup before making any destructive change, SHALL refuse
to run while another process is actively using `sessions.db`, and SHALL be safe to re-run without
duplicating or corrupting data.

#### Scenario: A backup exists before the destructive step runs
- **WHEN** the migration runs
- **THEN** a restorable copy of the pre-migration database exists on disk before the schema
  change is applied, and the migration's own output tells the user where it is

#### Scenario: The migration refuses to run against an actively-used database
- **WHEN** another process holds a write lock on `sessions.db` at the moment the migration is
  invoked
- **THEN** the migration refuses to proceed and reports that another process appears to be using
  the database, rather than racing that process's write

#### Scenario: Re-running a completed migration is a safe no-op
- **WHEN** the migration is run again after it has already completed successfully
- **THEN** it recognizes the prior completion and does not re-apply the schema change or
  re-import data a second time

### Requirement: Historical sessions are assigned a uuid without inventing false forks
The migration SHALL determine each existing session record's uuid from the most reliable
available evidence, and SHALL NOT leave a session in a state where it appears to be a fork of
itself the next time it is used.

#### Scenario: A session with exactly one matching transcript is resolved automatically
- **WHEN** an existing `sessions` record has exactly one transcript that can be identified as
  belonging to it
- **THEN** the migration assigns that transcript's uuid to the record without requiring manual
  intervention

#### Scenario: An unresolvable record is flagged, not guessed
- **WHEN** an existing `sessions` record has no identifiable transcript, or more than one
  transcript that could plausibly belong to it
- **THEN** the migration does not silently assign a uuid to it; it is reported as needing
  attention rather than resolved incorrectly

#### Scenario: A flagged record does not become a permanent phantom fork
- **WHEN** a record was left unresolved by the migration, and that same session is later opened
  again with its real uuid
- **THEN** the record is reconciled to the real uuid in place rather than producing a second,
  duplicate-looking entry that never goes away

### Requirement: Operators can inspect and resolve leftover migration-ambiguous records
The system SHALL provide a way to list and resolve `sessions` records the migration could not
confidently assign a uuid to.

#### Scenario: Listing ambiguous records after migration
- **WHEN** an operator asks to see remaining migration-ambiguous records
- **THEN** each such record is listed with enough information (project, tag, why it was
  ambiguous) to decide what to do with it

#### Scenario: Resolving an ambiguous record
- **WHEN** an operator chooses to resolve a specific ambiguous record (assign it a uuid, or
  remove it)
- **THEN** that action is applied and the record no longer appears in the ambiguous listing
