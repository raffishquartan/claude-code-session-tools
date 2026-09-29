## Purpose

Defines when `ccst pdata init` adopts an existing published sync dump instead of classifying and
importing a project's flat files, including under `--rehearse`.

## ADDED Requirements

### Requirement: Adoption is decided by the live machine's pdata content, also when rehearsing
`ccst pdata init` (dry run and `--write`) SHALL adopt a project's published sync dump only when the
machine's real per-project database has no records; when that database has records it SHALL
classify normally. This decision SHALL use the real database even when `--rehearse` redirects the
rehearsal's own database, proposal and backups to a sandbox.

#### Scenario: Rehearsing a project that already has local pdata and a dump
- **WHEN** the project has records in its real database and a valid `.pdata-db-dump/latest.sql`,
  and `ccst pdata init --project NAME --rehearse COPY` is run on a `cp -r` copy that lacks the
  real database
- **THEN** it produces the ordinary classification report and does not report adoption

#### Scenario: Rehearsing a project with a dump and no local pdata
- **WHEN** the project's real database has no records and a valid dump exists, and the command is
  run with `--rehearse`
- **THEN** it reports that `--write` will adopt the dump

#### Scenario: Rehearsal never writes the real database
- **WHEN** a rehearsal dry run consults the real database
- **THEN** no record in it is created, changed or deleted
