# pdata/dump-format-version Specification

## Purpose
Defines how a pdata database and the published sync dump identify their format, so a machine never
silently loads or publishes data in a format it does not understand.

## Requirements

### Requirement: Databases and dumps carry a format version
A pdata database SHALL record its format (integer ids = format 3, UUID ids = format 4; each format
is named for the ccst major version that introduced it, and a dump without a header is format 3) in a way that
survives dumping and rehydrating. A published dump SHALL carry a header line stating its format.
Format 4 dumps SHALL be published to `.pdata-db-dump/v4/` (`latest.sql`, `latest.sha256`,
`archive/`) in the project's pdata directory. Format 3 files in `.pdata-db-dump/` SHALL NOT be
modified or deleted by format 4 code.

#### Scenario: A migrated project publishes to the v4 location
- **WHEN** a format 4 database is published
- **THEN** `latest.sql` and `latest.sha256` are written under `.pdata-db-dump/v4/` and the format 3 files in `.pdata-db-dump/` are byte-for-byte unchanged

### Requirement: Rehydrate and adopt-from-dump refuse a format mismatch
`ccst pdata sync-check`, the sync hooks, the rehydrate path and `ccst pdata init` adoption SHALL
refuse to load a dump whose format differs from the local database's format, with a message that
names both formats and the command to run (`ccst pdata migrate-uuid`). A refusal SHALL leave the
local database unchanged and SHALL NOT be reported as a fork.

#### Scenario: An unmigrated database meets a format 4 dump
- **WHEN** a machine with a format 3 database finds a dump in `.pdata-db-dump/v4/`
- **THEN** sync-check reports a pending migration naming `ccst pdata migrate-uuid`, loads nothing, publishes nothing, and does not report a fork

#### Scenario: Adoption of a mismatched dump
- **WHEN** `ccst pdata init` would adopt a dump whose format differs from the format the new database would have
- **THEN** init refuses with the format-mismatch message instead of adopting

### Requirement: An unmigrated database is read-only except for migration and diagnostics
When a project's database is format 3, every `ccst pdata` verb that reads or writes records SHALL
refuse with a message directing the user to `ccst pdata migrate-uuid`; `migrate-uuid`, `sync-check`
(status only), `verify` and `ccst doctor` SHALL continue to work.

#### Scenario: A record verb on an unmigrated project
- **WHEN** a user runs `ccst pdata add` on a format 3 project after upgrading to 4.0.0
- **THEN** the command exits non-zero, writes nothing, and prints the migration command
