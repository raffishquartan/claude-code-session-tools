## ADDED Requirements

### Requirement: The standalone legacy import refuses to run against a pre-uuid `sessions.db`
The legacy flat-file import (`ccst sessions migrate`, run on its own) SHALL, when `sessions.db`
already contains a `sessions` table that has not been migrated to the uuid-aware schema, exit
non-zero without changing `sessions.db` (no tables, columns, or rows added or altered) and
without creating a backup, and SHALL tell the user to run `ccst sessions migrate-uuid --write`
first, from a plain terminal with no other `claude` session running. It SHALL apply this in
dry-run mode too. A corrupt `sessions.db` SHALL also produce a clean non-zero exit with a
message, not a traceback.

#### Scenario: Import against a pre-uuid database changes nothing and names the fix
- **WHEN** `sessions.db` has the pre-3.0.0 schema and legacy tag/activity/mute sources are
  present, and the user runs the standalone legacy import (with or without dry-run), whether or
  not any session has a resolvable transcript
- **THEN** the command exits non-zero, `sessions.db`'s tables, columns and row counts are
  unchanged, no backup is created, and the output names `ccst sessions migrate-uuid --write`

#### Scenario: Import after the schema migration succeeds
- **WHEN** the user has run `ccst sessions migrate-uuid --write` and then runs the legacy import
- **THEN** the import completes and records its completion marker

#### Scenario: Import with no database, or no `sessions` table, is unaffected
- **WHEN** `sessions.db` does not exist, or exists without a `sessions` table, and legacy sources
  are present
- **THEN** the import runs and the database ends with the uuid-aware schema

### Requirement: `ccst migrate all` orders the sessions rebuild before the legacy import
`ccst migrate all` SHALL, when `sessions.db` has a pre-uuid `sessions` table, perform the uuid
schema migration (with its usual backup and concurrent-writer guard) before the sessions legacy
import, so the two steps together succeed in one invocation. If the schema migration fails or is
refused, the sessions import SHALL NOT run, that step SHALL report a non-zero result, and the
`ccmsg`, `ccsched` and `telemetry` steps SHALL still run. In dry-run mode it SHALL report what the
rebuild would do, state that the import's own dry-run is skipped until the rebuild has run, and
SHALL NOT write anything.

#### Scenario: One `migrate all` migrates a pre-uuid database and imports legacy data
- **WHEN** `sessions.db` is pre-uuid, legacy sources are present, and the user runs
  `ccst migrate all`
- **THEN** the schema migration runs first, the legacy import then completes, both completion
  markers are recorded, and every legacy tag, activity timestamp and mute is present

#### Scenario: A refused rebuild skips only the sessions import
- **WHEN** the schema migration is refused (for example another process holds a write lock on
  `sessions.db`)
- **THEN** the sessions step reports the refusal and a non-zero result, the other three steps
  still run, the overall exit code is non-zero, and the closing text names the failed step
  instead of telling the user to re-run without dry-run

### Requirement: The schema migration validates its starting structure before changing anything
`ccst sessions migrate-uuid` SHALL, before any backup or rebuild, check that the `sessions` table
has the columns the rebuild copies (`project_dir`, `basename`, `start_date`, `last_opened`,
`last_active`, `discovered_at`) and SHALL refuse with a message naming each missing column
instead of failing part-way through with a raw database error. `updated_at` is optional. A missing
`sessions` table, a missing file, or an already uuid-keyed and marked database SHALL be a
successful no-op (a missing table is created in the current schema and marked). An already
uuid-keyed table whose completion marker is missing SHALL have the marker recorded without any
rebuild.

#### Scenario: Missing required column is named
- **WHEN** `sessions.db` has a `sessions` table missing a column the rebuild copies (for example
  `discovered_at`)
- **THEN** the command exits non-zero, changes nothing, creates no backup, and the output names
  the missing column

#### Scenario: Older schema without `updated_at` migrates
- **WHEN** `sessions.db` has a pre-3.0.0 `sessions` table with no `updated_at` column
- **THEN** both the dry-run and `--write` complete successfully and the migrated rows are
  preserved

#### Scenario: Database with no `sessions` table is not a dead end
- **WHEN** `sessions.db` exists but has no `sessions` table
- **THEN** the command exits zero, and afterwards the database has the current schema and the
  completion marker

#### Scenario: Uuid-keyed table with a missing marker is repaired without a rebuild
- **WHEN** `sessions` is already uuid-keyed (including with several rows sharing a tag) but the
  completion marker is missing
- **THEN** the command records the marker, leaves every row untouched, and exits zero

### Requirement: An upgrade from any shipped pre-3.0.0 schema completes
Starting from a `sessions.db` in either schema shape shipped before 3.0.0 (without and with the
`updated_at` column), with legacy tag, activity and mute sources present, running the schema
migration and then the legacy import (or `ccst migrate all`) SHALL succeed, record both completion
markers, and preserve every legacy tag, activity timestamp and mute.

#### Scenario: Each shipped shape upgrades
- **WHEN** a database in the v1.0.0-v2.12.x shape, and separately one in the v2.13.0-v2.14.x
  shape, is upgraded by the schema migration followed by the legacy import
- **THEN** both runs exit zero with both markers recorded and all legacy data present
