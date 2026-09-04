## Purpose

Defines the audit-timestamp and compare-and-swap (CAS) guarantees every common-store table (in
`ccmsg.db`, `ccsched.db`, `sessions.db`, `telemetry.db`, `command-cache.db`) must provide, so a
row's creation and last-modification time are always recoverable and a client can never silently
overwrite a concurrent update it never saw.

## ADDED Requirements

### Requirement: Every common-store table records creation time
Every table in the five common stores SHALL have a `created_at` column (or an existing
domain-specific column that already unambiguously records first-insert time, e.g. an
append-only event log's own timestamp column) set once, at insert, and never modified afterward.

#### Scenario: A new row records its creation time
- **WHEN** a row is inserted into any common-store table
- **THEN** that row's `created_at` (or equivalent first-insert timestamp column) reflects the
  insert time and is never changed by any later write to that row

### Requirement: Every table whose rows are updated in place records last-modification time
Every common-store table that is ever updated in place (as opposed to insert-only or
insert-then-delete tables) SHALL have an `updated_at` column that is set on every write that
mutates an existing row, including upserts.

#### Scenario: An existing row is updated
- **WHEN** an existing row in a table that supports in-place updates is updated (directly, or via
  an `INSERT ... ON CONFLICT DO UPDATE` upsert)
- **THEN** that row's `updated_at` reflects the time of that write

#### Scenario: Insert-only tables are exempt
- **WHEN** a table's rows are only ever inserted or deleted, never updated in place (e.g. an
  append-only event log)
- **THEN** that table is not required to carry an `updated_at` column

### Requirement: Race-prone tables support compare-and-swap updates
A common-store table whose rows can plausibly be updated by two concurrent processes reading the
same row before either writes (e.g. two `ccsched` invocations editing the same job, two
concurrent hook invocations updating the same session or cached command) SHALL support a
compare-and-swap update: the write is guarded by a version token read alongside the row, and the
write is rejected - not silently applied - if the row's version has changed since that read.

#### Scenario: A CAS write succeeds when the version is current
- **WHEN** a caller updates a row using the version it most recently read, and no other write has
  touched that row since
- **THEN** the update applies and the row's version advances

#### Scenario: A CAS write is rejected when the version is stale
- **WHEN** a caller updates a row using a version older than the row's current version (another
  writer updated it in between)
- **THEN** the update does not apply (zero rows change) and the caller can detect the rejection
  from the write's return value, rather than the write silently overwriting the intervening
  change

### Requirement: A single reusable CAS primitive backs every CAS-guarded table
Compare-and-swap support across the common stores SHALL be implemented via one shared primitive,
not a separate hand-rolled implementation per table.

#### Scenario: Two different stores' CAS-guarded tables both use the same primitive
- **WHEN** a CAS-guarded update is issued against a table in one store (e.g. `ccsched.db`'s
  `jobs`) and against a CAS-guarded table in a different store (e.g. `ccmsg.db`'s `messages`)
- **THEN** both writes are performed through the same shared compare-and-swap function, not two
  independent implementations of the same check-then-update logic
