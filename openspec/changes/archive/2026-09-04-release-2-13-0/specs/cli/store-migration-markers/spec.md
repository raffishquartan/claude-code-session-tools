## Purpose

Defines how `ccmsg`, `ccsched`, and `sessions` record and check whether their one-shot
legacy-data migration has run, so `ccst doctor` cannot report "already migrated" for a store that
has never been migrated but has simply accumulated normal-use rows since installation.

## ADDED Requirements

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
