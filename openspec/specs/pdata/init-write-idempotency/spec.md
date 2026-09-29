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

### Requirement: A file path the operating system rejects is a verification failure, not a crash
When `ccst pdata init --write` verifies an imported record's `file_path` and the operating system
cannot evaluate that path at all (for example because a name is too long), the run SHALL treat it
as a verification failure for that record - reporting the record, the path value and the system
error - and SHALL roll back the run's inserted rows exactly as for a path that simply does not
exist.

#### Scenario: An unevaluable file_path fails verification and rolls back
- **WHEN** a `db-owned` entry maps a `file_path_column` whose value cannot be evaluated by the
  operating system, so checking whether it exists raises an error instead of answering
- **THEN** `--write` reports a failure whose reasons name that record, the value and the system
  error, no rows from the run remain live, no backup is made, and the original files are untouched

### Requirement: An unexpected error after rows were inserted rolls them back
If `ccst pdata init --write` fails with an error that it does not report as a verification or
backup failure result, at any point after it has inserted rows and before its cutover starts, it
SHALL roll back every row that run inserted and then let the original error propagate, leaving no
live rows from the run, no manifest entry marked migrated, no cutover performed, and the source
files unchanged. This includes an interruption of the run by the user.

#### Scenario: An unexpected error during verification
- **WHEN** an error `--write` does not report as a verification failure occurs while verifying
  imported rows
- **THEN** the run's inserted rows are rolled back, the original error propagates, and the source
  files and manifest are unchanged

#### Scenario: An unexpected error while creating the backup
- **WHEN** creating the pre-cutover backup fails in a way that is not reported as a backup failure
  result
- **THEN** the run's inserted rows are rolled back, the original error propagates, and no
  cutover is performed, no manifest entry is marked migrated, and the source files are unchanged

#### Scenario: The user interrupts a long import
- **WHEN** the run is interrupted (Ctrl-C) after some rows were inserted
- **THEN** those rows are rolled back and the interruption propagates

### Requirement: A failing rollback never hides the original error
When rolling back after an error fails in whole or in part, `ccst pdata init --write` SHALL still
propagate the original error, and SHALL report which rows are still live by record id (in its
progress output and attached to the original error).

#### Scenario: Rollback cannot delete the rows
- **WHEN** an error occurs after rows were inserted and the rollback itself then fails
- **THEN** the original error is the one the caller sees, and the record ids still live are named

### Requirement: A source that cannot be re-counted is a verification failure
When the parity check cannot re-read a source file to count its rows (unreadable file, malformed
content, oversized field), `ccst pdata init --write` SHALL report a verification failure naming
the entry and the cause, and roll back the run's rows, as it does for the same errors during
import.

#### Scenario: Re-count fails
- **WHEN** re-counting an imported entry's source rows raises a read or parse error
- **THEN** `--write` returns a failure whose reasons name that entry, and no rows from the run
  remain live

### Requirement: A cutover failure never leaves rows a rerun would duplicate
If cutover fails part-way, `ccst pdata init --write` SHALL keep the rows of every entry whose
source file had already been moved into the archive and mark those entries migrated in the
manifest, SHALL roll back the rows of every entry whose source was still in place, and SHALL
propagate the original error, so that re-running imports each entry exactly once.

#### Scenario: Cutover fails before moving anything
- **WHEN** cutover fails before any source file has been moved
- **THEN** no rows from the run remain live, no entry is marked migrated, and re-running imports
  each row once

#### Scenario: Cutover fails after moving some files
- **WHEN** cutover fails after moving the first of two entries' sources
- **THEN** the first entry's rows stay live and it is marked migrated, the second entry's rows
  are rolled back and it is not marked, and the error propagates
