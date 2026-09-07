# pdata/init-write-idempotency Specification

## Purpose

Defines how `ccst pdata init --write` behaves when run again against a manifest that already has
cut-over entries, so a second write round for newly-fixed or newly-added files never re-imports or
rolls back already-migrated entries.

## Requirements

### Requirement: A successfully cut-over manifest entry is marked migrated
When `ccst pdata init --write` successfully imports, verifies, backs up, and cuts over a `db-owned`
manifest entry, the system SHALL record that success on the entry (a persisted migrated marker) and
persist the updated manifest to disk before returning.

#### Scenario: A first successful --write marks its entries
- **WHEN** `ccst pdata init --write` completes successfully for a project with N `db-owned`
  manifest entries and no prior migrated marker on any of them
- **THEN** all N entries carry a migrated marker in the manifest file on disk afterward

### Requirement: A later `--write` skips already-migrated entries instead of re-importing them
When `ccst pdata init --write` runs against a manifest containing both `db-owned` entries with a
migrated marker and `db-owned` entries without one, the system SHALL import only the entries
without a migrated marker, and SHALL NOT attempt to read or re-import an entry that already carries
one.

#### Scenario: A second --write round picks up only new entries
- **WHEN** `ccst pdata init --write` runs a second time against a manifest where some entries were
  cut over by a prior successful `--write` (and carry a migrated marker) and other entries are
  newly added or newly fixed (and carry no migrated marker)
- **THEN** only the entries without a migrated marker are imported, verified, and cut over; the
  already-migrated entries are left untouched and are not read from their (now-archived) original
  path

### Requirement: A rollback in a later `--write` never discards entries from a prior successful run
When a later `--write` run fails verification or backup for one or more of the entries it is
importing in that run, the system SHALL roll back only the record ids created during that run, and
SHALL NOT affect record ids or entries that were already committed and marked migrated by an
earlier successful `--write`.

#### Scenario: A partial second round rolls back only its own new failures
- **WHEN** a second `--write` round imports two new entries and one of them fails verification
- **THEN** only the record ids created for entries in this run are candidates for rollback; entries
  already marked migrated from the prior successful run are neither re-processed nor rolled back

### Requirement: A `db-owned` entry with a missing source and no migrated marker fails with a specific error
When `ccst pdata init --write` encounters a `db-owned` manifest entry whose source file does not
exist and which carries no migrated marker, the system SHALL fail that entry with a specific,
actionable error identifying the entry and stating that its source is missing and its
classification should be corrected if it was already migrated by another means, rather than
surfacing only a generic file-not-found error inside the verification-failure reasons list.

#### Scenario: An entry's source is missing but never migrated by this tool
- **WHEN** `ccst pdata init --write` runs against a manifest entry marked `db-owned` with no
  migrated marker, and that entry's source file does not exist on disk
- **THEN** the run reports a specific error naming the entry and explaining the source is missing
  and unmarked, distinguishable from the ordinary verification-failure report for a different kind
  of import error
