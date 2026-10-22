# Spec Delta

## Purpose
Defines what identifies a pdata record, so two machines can create records independently without
their ids ever colliding, and how the id appears at every user-visible surface.

## ADDED Requirements

### Requirement: A record id is a UUID string
Every pdata record SHALL be identified by a UUID in its canonical lowercase hyphenated text form.
A record created by `ccst pdata add` or by any internal writer SHALL receive a newly generated random
UUID, except for the records covered by "Deterministic ids". The id SHALL never be reassigned or
reused, including after a soft delete.

#### Scenario: A new record gets a UUID
- **WHEN** `ccst pdata add` creates a record
- **THEN** the id it prints and stores is a canonical UUID string, and two adds never print the same id

#### Scenario: Ids do not depend on the machine's existing records
- **WHEN** two machines each add a record to the same project while neither has seen the other's
- **THEN** the two records have different ids

### Requirement: The CLI accepts and prints ids as UUID strings
`ccst pdata get`, `update`, `delete` and `restore` SHALL take `--id` as a UUID string, and
`ccst pdata resolve --choice` SHALL take `<uuid>=local|dump`. An `--id` or `--choice` id that is not
a canonical UUID SHALL be rejected as a usage error naming the offending value, without touching the
database. Table, csv and json output SHALL show ids as UUID strings; json output SHALL use JSON
strings.

#### Scenario: A malformed id is a usage error
- **WHEN** a user runs `ccst pdata get --project p --id 12`
- **THEN** the command exits with a usage error that says `12` is not a UUID, and reads no data

#### Scenario: A well-formed id that does not exist is not found
- **WHEN** a user runs `ccst pdata get --project p --id <a valid UUID that no record has>`
- **THEN** the command reports `record not found` with that id, exactly as for any missing record

### Requirement: Record listings have a stable order that does not depend on the id value
Any listing or query that previously ordered by id SHALL order by `created_at` ascending, then id
ascending as a tie-break, so the order is deterministic and meaningful.

#### Scenario: Listing order follows creation time
- **WHEN** three records were created at times t1 < t2 < t3 and listed
- **THEN** they appear in the order t1, t2, t3 regardless of their id values

### Requirement: Optimistic locking is unchanged by the id type
`update` and `delete` SHALL continue to require the caller's expected `version` and SHALL fail with
the existing version-conflict outcome (exit code 3) when it does not match; `restore` SHALL continue
to bump the version. Only the identifier in the lookup changes.

#### Scenario: A stale version is still rejected
- **WHEN** `ccst pdata update` is given the id of a record and a stale `--version`
- **THEN** the command exits with the version-conflict code and changes nothing

### Requirement: Records that are unique by meaning have deterministic ids
The session-output watermark record of a project SHALL have an id derived deterministically from the
project name. Each registered session-output record SHALL have an id derived deterministically from
the project name and its `file_path`. Two machines that independently create the watermark, or
independently register the same output file, SHALL therefore create the same record id, and
registering an already-registered file SHALL NOT create a second record.

#### Scenario: Two machines create the watermark
- **WHEN** machine A and machine B each create the session-output watermark for the same project
- **THEN** both records have the same id, so reconciling them is an edit conflict on one record, not two watermark rows

#### Scenario: Two machines register the same output file
- **WHEN** both machines reconcile session outputs and both find `cc-sessions/x/out/report.md`
- **THEN** both create a record with the same id, and neither machine's database ends up with two records for that file

### Requirement: Fork classification no longer treats an id match as a collision
When two databases that share a history have diverged, a record present on both sides under the same
id SHALL be treated as the same logical record: if the contents differ it is an edit conflict, if
one side deleted it and the other updated it, a delete-versus-update conflict, and if its record
group differs, a group mismatch. The `id collision` classification, which inferred two unrelated
records from the same integer id and different creation times, SHALL no longer exist, and
`ccst pdata resolve` SHALL NOT print the out-of-band reconciliation message that went with it.

#### Scenario: Both machines add different records
- **WHEN** the local database and the dump each gained records the other lacks, with different ids
- **THEN** `ccst pdata resolve` lists those records as present on one side only, and no record is reported as an id collision

#### Scenario: The same id with different content
- **WHEN** both sides hold a record with the same id and different content
- **THEN** the record is reported as an edit conflict that the user resolves with `local` or `dump`
