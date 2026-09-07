## Purpose

Defines the pointer file `ccst pdata init --write` leaves behind at a migrated entry's original
path by default, so anything else in the project that cites the old path by name finds a live
signpost instead of a silent 404, and the opt-out flag for suppressing it.

## ADDED Requirements

### Requirement: Cutover writes a pointer file at each migrated entry's original path by default
When `ccst pdata init --write` cuts over a `db-owned` manifest entry, the system SHALL, by default,
write a Markdown pointer file at the entry's original path (same basename, `.md` extension) stating
which pdata `record_group` now holds the data, a field/schema table for that group, and an example
`ccst pdata` query command for it.

#### Scenario: A cutover entry gets a pointer file
- **WHEN** `ccst pdata init --write` cuts over a `db-owned` entry at `analysis/example.csv` into
  record_group `example`, with no `--leave-no-pointer-files` flag passed
- **THEN** a file `analysis/example.md` exists afterward, naming record_group `example`, listing
  its fields, and showing an example query command for it

### Requirement: A pointer file includes preserved preface text when the entry carries it
When a cut-over manifest entry carries removed preface/provenance text (recorded during manual
review, e.g. via the garbled-CSV-header fix recipe), the system SHALL include that text verbatim
under its own heading in the entry's pointer file.

#### Scenario: A garbled-header entry's pointer file preserves the removed text
- **WHEN** `ccst pdata init --write` cuts over an entry whose manifest record carries preserved
  preface text from a stripped garbled header
- **THEN** the resulting pointer file includes that text verbatim under its own heading, in
  addition to the record_group/schema/example-query content every pointer file has

### Requirement: `--leave-no-pointer-files` suppresses pointer-file creation
`ccst pdata init --write` SHALL accept a `--leave-no-pointer-files` flag; when passed, the system
SHALL NOT write a pointer file for any entry cut over in that run, and `ccst pdata init --help`
SHALL describe both the default-on pointer-file behavior and this opt-out flag.

#### Scenario: A write run opts out of pointer files
- **WHEN** `ccst pdata init --project <name> --write --leave-no-pointer-files` cuts over one or
  more `db-owned` entries
- **THEN** no pointer file is created at any of those entries' original paths

#### Scenario: Help text documents the default and the opt-out
- **WHEN** a user runs `ccst pdata init --help`
- **THEN** the output describes that pointer files are written by default on cutover and that
  `--leave-no-pointer-files` suppresses them
