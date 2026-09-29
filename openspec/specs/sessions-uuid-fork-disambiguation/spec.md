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

### Requirement: The standalone legacy import refuses to run against a pre-uuid `sessions.db`
The legacy flat-file import (`ccst sessions migrate`, run on its own) SHALL, when `sessions.db`
already contains a `sessions` table that has not been migrated to the uuid-aware schema, exit
non-zero without changing `sessions.db` (no tables, columns, or rows added or altered) and
without creating a backup, and SHALL tell the user to run `ccst sessions migrate-uuid --write`
first, from a plain terminal with no other `claude` session running. It SHALL apply this in
dry-run mode too. A corrupt `sessions.db` SHALL also produce a clean non-zero exit with a
message, not a traceback.

#### Scenario: Import against a pre-uuid database changes nothing and names the fix
- **WHEN** `sessions.db` has the pre-3.0.0 schema and legacy tag/activity/mute sources are
  present, and the user runs the standalone legacy import (with or without dry-run), whether or
  not any session has a resolvable transcript
- **THEN** the command exits non-zero, `sessions.db`'s tables, columns and row counts are
  unchanged, no backup is created, and the output names `ccst sessions migrate-uuid --write`

#### Scenario: Import after the schema migration succeeds
- **WHEN** the user has run `ccst sessions migrate-uuid --write` and then runs the legacy import
- **THEN** the import completes and records its completion marker

#### Scenario: Import with no database, or no `sessions` table, is unaffected
- **WHEN** `sessions.db` does not exist, or exists without a `sessions` table, and legacy sources
  are present
- **THEN** the import runs and the database ends with the uuid-aware schema

### Requirement: `ccst migrate all` orders the sessions rebuild before the legacy import
`ccst migrate all` SHALL, when `sessions.db` has a pre-uuid `sessions` table, perform the uuid
schema migration (with its usual backup and concurrent-writer guard) before the sessions legacy
import, so the two steps together succeed in one invocation. If the schema migration fails or is
refused, the sessions import SHALL NOT run, that step SHALL report a non-zero result, and the
`ccmsg`, `ccsched` and `telemetry` steps SHALL still run. In dry-run mode it SHALL report what the
rebuild would do, state that the import's own dry-run is skipped until the rebuild has run, and
SHALL NOT write anything.

#### Scenario: One `migrate all` migrates a pre-uuid database and imports legacy data
- **WHEN** `sessions.db` is pre-uuid, legacy sources are present, and the user runs
  `ccst migrate all`
- **THEN** the schema migration runs first, the legacy import then completes, both completion
  markers are recorded, and every legacy tag, activity timestamp and mute is present

#### Scenario: A refused rebuild skips only the sessions import
- **WHEN** the schema migration is refused (for example another process holds a write lock on
  `sessions.db`)
- **THEN** the sessions step reports the refusal and a non-zero result, the other three steps
  still run, the overall exit code is non-zero, and the closing text names the failed step
  instead of telling the user to re-run without dry-run

### Requirement: The schema migration validates its starting structure before changing anything
`ccst sessions migrate-uuid` SHALL, before any backup or rebuild, check that the `sessions` table
has the columns the rebuild copies (`project_dir`, `basename`, `start_date`, `last_opened`,
`last_active`, `discovered_at`) and SHALL refuse with a message naming each missing column
instead of failing part-way through with a raw database error. `updated_at` is optional. A missing
`sessions` table, a missing file, or an already uuid-keyed and marked database SHALL be a
successful no-op (a missing table is created in the current schema and marked). An already
uuid-keyed table whose completion marker is missing SHALL have the marker recorded without any
rebuild.

#### Scenario: Missing required column is named
- **WHEN** `sessions.db` has a `sessions` table missing a column the rebuild copies (for example
  `discovered_at`)
- **THEN** the command exits non-zero, changes nothing, creates no backup, and the output names
  the missing column

#### Scenario: Older schema without `updated_at` migrates
- **WHEN** `sessions.db` has a pre-3.0.0 `sessions` table with no `updated_at` column
- **THEN** both the dry-run and `--write` complete successfully and the migrated rows are
  preserved

#### Scenario: Database with no `sessions` table is not a dead end
- **WHEN** `sessions.db` exists but has no `sessions` table
- **THEN** the command exits zero, and afterwards the database has the current schema and the
  completion marker

#### Scenario: Uuid-keyed table with a missing marker is repaired without a rebuild
- **WHEN** `sessions` is already uuid-keyed (including with several rows sharing a tag) but the
  completion marker is missing
- **THEN** the command records the marker, leaves every row untouched, and exits zero

### Requirement: An upgrade from any shipped pre-3.0.0 schema completes
Starting from a `sessions.db` in either schema shape shipped before 3.0.0 (without and with the
`updated_at` column), with legacy tag, activity and mute sources present, running the schema
migration and then the legacy import (or `ccst migrate all`) SHALL succeed, record both completion
markers, and preserve every legacy tag, activity timestamp and mute.

#### Scenario: Each shipped shape upgrades
- **WHEN** a database in the v1.0.0-v2.12.x shape, and separately one in the v2.13.0-v2.14.x
  shape, is upgraded by the schema migration followed by the legacy import
- **THEN** both runs exit zero with both markers recorded and all legacy data present
