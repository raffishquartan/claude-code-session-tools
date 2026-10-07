# pdata/date-field-warning Specification

## Purpose
Stops mixed date forms entering a project's data store by warning, without blocking, when a
non-ISO value is written to a date-like field.

## Requirements

### Requirement: Non-ISO values written to date-like fields produce a warning
When `ccst pdata add` or `ccst pdata update` writes a value to an extension field that is
date-like (a TEXT field; numeric fields are never checked, because an integer `_at` field holds
an epoch), and the value is not an ISO date form, the command SHALL print a warning on stderr
naming the record group, the field and the offending value, and SHALL still perform the write and
exit with the status it would have had without the warning. A field is date-like when its name
ends `_at` or `_date`, or its description (from the record group's field registry) contains the
word "date" (case-insensitive); a field whose name ends `_text` is never date-like. An ISO date
form is `yyyy-MM-dd`, an ISO datetime with `T` or a space separator, a partial `yyyy-MM` or `yyyy`,
or a range `yyyy-MM-dd..yyyy-MM-dd`. An empty value and a `null` assignment SHALL NOT warn.

#### Scenario: Dotted date into a date field
- **WHEN** `ccst pdata add --field event_date=2026.10.05` writes to a field named `event_date`
- **THEN** a warning on stderr names the group, `event_date` and `2026.10.05`, the record is
  written, and the exit status is 0

#### Scenario: Field is date-like through its description
- **WHEN** a field `due` has the description "due date" and `--field due="6 Sep 2024"` is written
- **THEN** a warning is printed and the record is written

#### Scenario: ISO values do not warn
- **WHEN** a date-like field receives `2026-10-05`, `2026-10-05 14:30`, `2026-10-05T14:30:00Z`,
  `2026-10` or `2026`
- **THEN** no warning is printed

#### Scenario: Verbatim text fields never warn
- **WHEN** a field named `date_text` receives `6th of September`
- **THEN** no warning is printed

#### Scenario: Numeric fields never warn
- **WHEN** an `INTEGER` field named `sent_at` receives `1790000000`
- **THEN** no warning is printed

#### Scenario: Non-date fields never warn
- **WHEN** a field whose name does not end `_at` or `_date` and whose description does not mention
  "date" receives `2026.10.05`
- **THEN** no warning is printed

#### Scenario: Update path warns the same way
- **WHEN** `ccst pdata update` sets a date-like field to a non-ISO value
- **THEN** the same warning is printed and the update still succeeds

### Requirement: The date-field check never changes a write's outcome
The check that produces date-field warnings SHALL NOT make `ccst pdata add` or `update` fail. If
reading the schema for the check errors, no warning SHALL be printed and the command SHALL return
the status the write produced.

#### Scenario: Schema read fails after the write
- **WHEN** a record has been written and reading the schema for the warning check raises a
  database error
- **THEN** no warning is printed and the command exits with the write's own status
