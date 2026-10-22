# pdata/uuid-migration Specification

## Purpose
Defines how an existing integer-id pdata installation is converted to UUID ids, safely and
identically on every machine that shares a project through the published dump.

## Requirements

### Requirement: `ccst pdata migrate-uuid` is a dry run unless `--write` is given
`ccst pdata migrate-uuid` SHALL take `--project NAME` or `--all-projects`, and `--dry-run` or
`--write` (mutually exclusive). With neither flag it SHALL behave as `--dry-run`. A dry run SHALL
print, per project, the plan (record counts per record group including soft-deleted rows, extension
tables and verify-watermark rows to be rewritten), every precondition and whether it holds, and
SHALL change nothing on disk.

#### Scenario: Default is a dry run
- **WHEN** a user runs `ccst pdata migrate-uuid --project p`
- **THEN** the plan is printed, exit status is 0 if every precondition holds, and the database and dump are unchanged

### Requirement: Migration runs only from a converged, quiet state
`--write` SHALL refuse, per project and without changing anything, unless all of these hold:
the database is format 3; the local database equals the published format 3 dump (vector clocks
equal, no unpublished local changes, no fork); no `claude` session occupies the project; the
database is not locked by another process. Each refusal SHALL name the unmet condition and the
remedy (for example `ccst pdata sync-check`, closing running `claude` sessions, or resolving the fork
with the 3.x tooling).

#### Scenario: Local changes not yet published
- **WHEN** the database has records added since the last dump was published
- **THEN** `--write` refuses, says to publish first, and leaves the database untouched

#### Scenario: Unresolved fork
- **WHEN** the local database and the dump have diverged
- **THEN** `--write` refuses, says the fork must be resolved first, and leaves the database untouched

#### Scenario: A claude session is running in the project
- **WHEN** a `claude` process is running with the project as its working directory
- **THEN** `--write` refuses and names closing running `claude` sessions

#### Scenario: A project with no database
- **WHEN** the project has no pdata database
- **THEN** the command reports there is nothing to migrate and exits 0

### Requirement: The id mapping is deterministic
Each integer id SHALL map to the UUIDv5 of the string `<project>:<integer id>` in a fixed namespace
UUID defined by the product. Two machines that migrate the same converged integer database SHALL
therefore produce identical UUIDs. The mapping SHALL be applied to `records.id`, to every extension
table's `record_id`, and to the verify watermark's `record_id`, for live and soft-deleted rows, and
SHALL leave every other column (version, timestamps, content, file_path, record_group) unchanged.

#### Scenario: Two machines migrate independently
- **WHEN** machines A and B hold identical converged format 3 databases for project p and each runs `migrate-uuid --write`
- **THEN** both databases contain the same set of UUID ids for the same records

#### Scenario: Soft-deleted rows are migrated
- **WHEN** a record has `deleted_at` set
- **THEN** it receives its mapped UUID and keeps its `deleted_at`, and the row counts per record group before and after are equal

### Requirement: Migration backs up first and publishes the format 4 dump
Before changing anything `--write` SHALL back up the database to a timestamped file under the
database directory's `migration-backups/` and verify the backup opens. After a successful rewrite
it SHALL run the project's integrity verification, record a completion marker, and publish the
format 4 dump with a header recording the format 3 vector clock it was migrated from. If the
backup, rewrite, verification or publish fails, the original database SHALL remain in place (or be
restored from the backup when the failure follows the rewrite's commit) and the error SHALL name
the backup. The error SHALL say the database is unchanged only when the rewrite never committed;
when restoring the backup itself fails, it SHALL say so and name the backup to restore by hand. A
failed publish SHALL leave no partial format 4 dump: each dump file is written through a temporary
file and a rename, and the files of an unfinished first publish are removed.

#### Scenario: Backup is taken and named
- **WHEN** `--write` succeeds
- **THEN** the output names the backup path, and the backup is a readable format 3 database with the original row counts

#### Scenario: Failure leaves the original database
- **WHEN** the rewrite fails part way
- **THEN** the project database is the original format 3 database and the error names the backup

#### Scenario: Publish interrupted between its two files
- **WHEN** the publish fails after writing `latest.sql` and before `latest.sha256`
- **THEN** the database is restored to format 3, no `.pdata-db-dump/v4/` dump remains, and a re-run migrates by the rewrite path

### Requirement: Migration is idempotent and resumable
Running `--write` on an already migrated project SHALL report that nothing needs doing and exit 0,
changing nothing. If a previous run converted the database but did not record the marker or publish
the dump, a re-run SHALL complete exactly those steps. Until the marker is recorded, a converted
database SHALL be treated as pending migration everywhere else: no `ccst pdata` record verb,
`dump`, sync-check or sync hook reads, publishes or syncs it.

#### Scenario: Second run
- **WHEN** `--write` is run twice
- **THEN** the second run reports `already migrated` and changes no file

#### Scenario: Sync during an unfinished migration
- **WHEN** sync-check or the SessionEnd hook runs on a converted database whose marker is not recorded
- **THEN** it reports a pending migration naming `ccst pdata migrate-uuid --write`, publishes nothing, and a re-run of `migrate-uuid --write` finishes the migration

### Requirement: A machine joining after the first migration adopts it
When a format 4 dump already exists for a project, `migrate-uuid --write` on another machine SHALL
first check that its local format 3 database matches the format 3 vector clock recorded in that
dump's header. If it matches, the machine SHALL end with the same data as the dump; if it does not
match (it has format 3 changes the migrated lineage never saw), `--write` SHALL refuse, state that
those changes must be re-entered after adopting the format 4 dump, and leave the database unchanged.
That a database or dump was migrated from format 3 SHALL be recorded explicitly (a
`-- migrated_from_format=3` header line and a database row), independent of the recorded vector,
so a format 3 database whose vector clock is empty migrates and is joined like any other; a dump
with no such record is not a migration lineage and no format 3 database matches it.

#### Scenario: Second machine matches the migrated lineage
- **WHEN** machine B is converged with the format 3 state that machine A migrated from
- **THEN** `migrate-uuid --write` on B succeeds and B's records equal the format 4 dump

#### Scenario: The format 3 vector clock was empty
- **WHEN** the first machine migrates a converged format 3 database whose vector clock is empty, and a second machine holds the same format 3 data
- **THEN** the format 4 dump records an empty origin, and the second machine's dry run plans adoption

#### Scenario: Second machine has changes the lineage never saw
- **WHEN** machine B published format 3 changes after machine A migrated
- **THEN** `--write` on B refuses with the explanation and does not touch B's database

### Requirement: `ccst doctor` and the pending-migration hook report unmigrated projects
`ccst doctor` SHALL produce a `migration-to-4.0.0:pdata-uuid` result per project: OK when the
project has no database or is format 4 with the marker recorded, FAIL when the database is format 3
(naming `ccst pdata migrate-uuid --write` to be run from a plain terminal, and the runbook's
location: its path relative to the repository root, `docs/pdata-uuid-migration-runbook.md`),
FAIL when the format and the marker disagree. The `pending-migration` hook SHALL surface an unmigrated project in its
session-start context, subject to the existing doctor mute list.

#### Scenario: An unmigrated project
- **WHEN** `ccst doctor` runs and project p is format 3
- **THEN** `migration-to-4.0.0:pdata-uuid` for p is FAIL and names the command and the runbook location

#### Scenario: A migrated project
- **WHEN** project p is format 4 with the marker recorded
- **THEN** its check is OK

### Requirement: Installing or upgrading announces the required migration
When `install-everything.sh` finishes and any pdata project is still format 3, it SHALL print a
framed, unmissable banner before its final "Done" text that says migration is required before any
`claude` session is started, lists the unmigrated projects, and gives the exact commands (the dry
run, then `--write`) and the runbook path. When every project is migrated or none has a database,
it SHALL print no such banner. A plain upgrade (`uv tool install cc-session-tools --upgrade`) that
bypasses the script SHALL still be caught by the format gate, the `pending-migration` hook and
`ccst doctor`.

#### Scenario: Install with an unmigrated project
- **WHEN** `install-everything.sh` completes and project p is format 3
- **THEN** its output ends with a banner naming p, `ccst pdata migrate-uuid --all-projects`, and `--write`, and states migration must happen before starting any `claude` session

#### Scenario: Install with everything migrated
- **WHEN** `install-everything.sh` completes and no project is format 3
- **THEN** no migration banner is printed

### Requirement: A machine may migrate on a later day if it made no format 3 writes meanwhile
`migrate-uuid --write` on a machine that upgrades after another machine already published the
format 4 dump SHALL succeed whenever the machine's format 3 state is the one recorded in that dump's
header, regardless of how much time has passed. The runbook SHALL instruct the user to disable the
pdata scheduled jobs on any machine that will migrate later than the first, so that no unattended
format 3 write happens in between.

#### Scenario: Upgrade days later, no writes
- **WHEN** machine B, whose scheduled pdata jobs were disabled, runs `migrate-uuid --write` three days after machine A
- **THEN** the migration succeeds and B ends with the format 4 dump's data

### Requirement: A committed terminal-only runbook accompanies the migration
The repository SHALL contain `docs/pdata-uuid-migration-runbook.md`, a step-by-step checklist with
exact shell commands, expected outputs and a troubleshooting section, written to be followed from a
plain terminal on each machine after ccst is upgraded and before its databases are migrated, with
no `claude` session. Every command in it SHALL be runnable on an unmigrated database (the
exempt verbs and `ccsched`), and the install banner SHALL name its path.

#### Scenario: Following the runbook with no claude session
- **WHEN** a user on an upgraded but unmigrated machine follows the runbook from a plain terminal
- **THEN** every command in it runs without error on the unmigrated databases, and the machine ends migrated with `ccst doctor` reporting OK for `migration-to-4.0.0:pdata-uuid`

### Requirement: Rollback is available from the backup and the untouched format 3 dump
The migration SHALL NOT delete or modify format 3 dump files or archives. Documentation SHALL state
how to roll back one machine (install 3.x, restore the named backup over the project database) and
SHALL state that format 3 archives remain loadable by 3.x for as long as they are retained.

#### Scenario: The format 3 dump after migration
- **WHEN** a project has been migrated
- **THEN** every file under `.pdata-db-dump/` outside `v4/` is byte-for-byte what it was before the migration
