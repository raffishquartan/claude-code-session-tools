# cli/store-audit-columns Specification

## Purpose

Defines the audit-timestamp and compare-and-swap (CAS) guarantees every common-store table (in
`ccmsg.db`, `ccsched.db`, `sessions.db`, `telemetry.db`, `command-cache.db`) must provide, so a
row's creation and last-modification time are always recoverable and a client can never silently
overwrite a concurrent update it never saw.

## Requirements

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
A common-store table whose rows are edited via a read-then-decide-then-write workflow - a caller
reads a row's current content, computes a new value from what it read, and writes that back (e.g.
two `ccsched` invocations both running `ccsched edit` against the same job) SHALL support a
compare-and-swap update: the write is guarded by a version token read alongside the row, and the
write is rejected - not silently applied - if the row's version has changed since that read. A
table whose writes are direct field-sets, single atomic statements (e.g. an `UPDATE ... SET
count=count+1` or an upsert with no prior read), or already guarded by a domain-specific
conditional (e.g. `WHERE status='sent'`) is not required to support compare-and-swap, since none
of those write shapes has a read-then-write staleness gap for it to close.

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
not a separate hand-rolled implementation per table - including when only one table currently
needs it, so a second CAS-guarded table added later reuses the same function rather than growing
a second implementation.

#### Scenario: A CAS-guarded table's write goes through the shared primitive
- **WHEN** a CAS-guarded update is issued against any common-store table (e.g. `ccsched.db`'s
  `jobs`)
- **THEN** that write is performed through the same shared compare-and-swap function every other
  CAS-guarded table in the common stores uses, not a table-specific hand-rolled implementation of
  the same check-then-update logic
