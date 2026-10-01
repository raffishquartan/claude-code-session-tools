## MODIFIED Requirements

### Requirement: Cutover writes a pointer file at each migrated entry's original path by default
When `ccst pdata init --write` cuts over a `db-owned` manifest entry, the system SHALL, by default,
write a Markdown pointer file at the entry's original path (same basename, `.md` extension) stating
which pdata `record_group` now holds the data, a field/schema table for that group, and an example
`ccst pdata` query command for it, unless that path is occupied by a file that is not this entry's
own earlier pointer, in which case the collision requirements below apply.

#### Scenario: A cutover entry gets a pointer file
- **WHEN** `ccst pdata init --write` cuts over a `db-owned` entry at `analysis/example.csv` into
  record_group `example`, with no `--leave-no-pointer-files` flag passed and no file at
  `analysis/example.md`
- **THEN** a file `analysis/example.md` exists afterward, naming record_group `example`, listing
  its fields, and showing an example query command for it

## ADDED Requirements

### Requirement: Cutover never overwrites an existing file at the pointer path
`ccst pdata init --write` SHALL NOT overwrite, truncate, move or delete any existing file in order
to write a pointer file. When the primary pointer path (`<stem>.md` beside the entry's original
path) holds a file that is not this entry's own earlier pointer, the system SHALL write the pointer
to `<stem>.pdata-pointer.md` instead and leave the existing file byte-for-byte unchanged. When that
substitute path also holds a file that is not this entry's own earlier pointer, the system SHALL skip
the pointer for that entry, still migrate its data, and warn.

#### Scenario: A real .md sits beside the migrated CSV
- **WHEN** `data/things.csv` is cut over and `data/things.md` already exists with other content
- **THEN** `data/things.md` is unchanged byte-for-byte, and `data/things.pdata-pointer.md` exists
  with the record_group, schema table and example query

#### Scenario: The substitute path is also occupied
- **WHEN** `data/things.csv` is cut over and both `data/things.md` and `data/things.pdata-pointer.md`
  exist and are not this entry's pointer
- **THEN** both files are unchanged, no pointer is written for the entry, its data is migrated, and
  the write report warns that its pointer was skipped

#### Scenario: Two entries share a stem
- **WHEN** `data/a.csv` and `data/a.tsv` are both cut over in one run
- **THEN** each gets its own pointer (the second at `data/a.pdata-pointer.md`) and neither
  overwrites the other

#### Scenario: No collision is unchanged
- **WHEN** nothing exists at the primary pointer path
- **THEN** the pointer is written there exactly as before and the write report has no pointer
  warnings

### Requirement: An entry's own earlier pointer may be rewritten
A file at a pointer path whose first line is exactly this entry's pointer heading
(`# <original file name> — migrated to pdata`) SHALL be treated as that entry's own earlier pointer
and MAY be rewritten, so repeated runs neither lose the pointer nor create duplicate pointer files.

#### Scenario: Idempotent re-run
- **WHEN** `--write` is run again after a run that wrote `data/things.pdata-pointer.md` beside a real
  `data/things.md`
- **THEN** the real `data/things.md` is still unchanged and no additional pointer file exists

### Requirement: Substituted and skipped pointers are reported
The write report SHALL list every entry whose pointer went to a substitute path or was skipped, with
the occupied path, and the archive `MANIFEST.md` line for that entry SHALL record it.

#### Scenario: Substitution is visible
- **WHEN** a pointer is written to `data/things.pdata-pointer.md` because `data/things.md` exists
- **THEN** the write report names `data/things.md` as occupied and `data/things.pdata-pointer.md` as
  the pointer path, and the entry's `MANIFEST.md` line says the pointer was substituted

### Requirement: `--leave-no-pointer-files` never touches existing files
With `--leave-no-pointer-files`, the system SHALL write no pointer file for any entry and SHALL leave
every existing file at a would-be pointer path unchanged.

#### Scenario: Opt-out beside a real .md
- **WHEN** `--write --leave-no-pointer-files` cuts over `data/things.csv` and `data/things.md` exists
- **THEN** `data/things.md` is unchanged and no pointer file is created
