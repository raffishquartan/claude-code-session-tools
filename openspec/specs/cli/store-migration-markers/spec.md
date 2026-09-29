# cli/store-migration-markers Specification

## Purpose

Defines how `ccmsg`, `ccsched`, and `sessions` record and check whether their one-shot
legacy-data migration has run, so `ccst doctor` cannot report "already migrated" for a store that
has never been migrated but has simply accumulated normal-use rows since installation.

## Requirements

### Requirement: Each store records an explicit, durable migration-completion marker
The `ccmsg`, `ccsched`, and `sessions` one-shot legacy-data migrations SHALL each record an
explicit completion marker (matching `telemetry.db`'s existing marker mechanism) in the same
database, and SHALL NOT record it until the migration's data has been written and verified.

#### Scenario: A migration only marks itself complete after its data is verified
- **WHEN** one of the three migrations finishes writing its data
- **THEN** the completion marker is recorded only after that data passes the migration's own
  verification step, never before or instead of it - so a verification failure never leaves a
  store marked complete on data that was not actually confirmed migrated

#### Scenario: Re-running a completed migration never duplicates data
- **WHEN** a migration is run again after its marker is already recorded and its legacy source
  still has content to read
- **THEN** it either refuses to run a second time (matching `migrate telemetry`'s existing
  refusal behavior) or, for a migration whose writes are idempotent by construction, safely
  re-applies them without duplicating or corrupting existing rows - never re-importing content as
  new

### Requirement: `ccst doctor` reads the marker directly for all four stores
`ccst doctor`'s pending-migration check SHALL determine migration completion for `ccmsg`,
`ccsched`, `sessions`, and `telemetry` by reading each store's explicit completion marker, not by
inferring completion from whether the new store contains any rows.

#### Scenario: An installed-but-unmigrated store is correctly flagged
- **WHEN** a store has accumulated rows from normal CCST use (e.g. `session_tags` rows written by
  the session-tag hook) but its legacy-data migration has never run and no completion marker is
  present
- **THEN** `ccst doctor` reports the migration as pending, not already complete

#### Scenario: A migrated store is correctly recognized as complete
- **WHEN** a store's completion marker is present
- **THEN** `ccst doctor` reports that store's migration as complete, regardless of row counts

### Requirement: A store with no legacy data to migrate is not permanently flagged pending
When a store's legacy data sources are already absent on the current machine (nothing to
migrate), the migration SHALL be considered complete rather than leaving `ccst doctor` reporting
a pending migration indefinitely.

#### Scenario: A fresh machine with no legacy sources is not flagged
- **WHEN** a store connects for the first time on a machine where that store's legacy data
  sources were never present
- **THEN** the completion marker is recorded (or the check otherwise treats the store as
  migrated) rather than `ccst doctor` reporting a permanently pending migration for data that
  will never exist on this machine

### Requirement: Pending-migration guidance for `sessions` names a working order
When the legacy flat-file import and the uuid schema migration are both pending for `sessions`,
`ccst doctor` SHALL report both findings from the same `sessions.db`, and each finding's guidance
SHALL say that the rebuild and the import go together and that `ccst migrate all` performs them in
the working order. Guidance SHALL NOT direct the user to a command that would refuse or fail in
the current state.

#### Scenario: Legacy import FAIL notes it will rebuild first
- **WHEN** legacy sessions data is unmigrated and `sessions.db` exists with the pre-uuid schema
- **THEN** the `migration-to-1.0.0:sessions` finding is a FAIL whose reason names
  `ccst migrate all` and states that it first rebuilds `sessions.db` (taking a backup)

#### Scenario: Legacy import FAIL is unchanged when the schema is already current
- **WHEN** legacy sessions data is unmigrated and `sessions.db` is absent or already uuid-aware
- **THEN** the `migration-to-1.0.0:sessions` finding reads as before

#### Scenario: Schema-migration FAIL mentions the pending import
- **WHEN** `sessions.db` is pre-uuid (in either failing state of the 3.0.0 check) and legacy
  sessions data is also unmigrated
- **THEN** the `migration-to-3.0.0:sessions-uuid` FAIL states that the legacy import is also
  pending and that `ccst migrate all` performs both
