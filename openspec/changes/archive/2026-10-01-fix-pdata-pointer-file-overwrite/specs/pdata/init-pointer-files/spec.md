## MODIFIED Requirements

### Requirement: Cutover writes a pointer file at each migrated entry's original path by default
When `ccst pdata init --write` cuts over a `db-owned` manifest entry, the system SHALL, by default,
write a Markdown pointer file at the entry's original path (same basename, `.md` extension) stating
which pdata `record_group` now holds the data, a field/schema table for that group, and an example
`ccst pdata` query command for it, unless that path is occupied by something that is not this
entry's own earlier pointer, in which case the collision requirements below apply.

#### Scenario: A cutover entry gets a pointer file
- **WHEN** `ccst pdata init --write` cuts over a `db-owned` entry at `analysis/example.csv` into
  record_group `example`, with no `--leave-no-pointer-files` flag passed and nothing at
  `analysis/example.md`
- **THEN** a file `analysis/example.md` exists afterward, naming record_group `example`, listing
  its fields, and showing an example query command for it, with exactly the content the pointer
  generator produces

## ADDED Requirements

### Requirement: Cutover never overwrites an existing file at the pointer path
`ccst pdata init --write` SHALL NOT overwrite, truncate, follow, move or delete anything that
exists at a pointer path in order to write a pointer file. When the primary pointer path
(`<stem>.md` beside the entry's original path) is occupied by anything other than this entry's own
earlier pointer (a regular file, a directory, a live or dangling symlink, or an unreadable file),
the system SHALL write the pointer to `<original file name>.pdata-pointer.md` in the same directory
instead and leave the occupant unchanged. When that substitute path is also occupied by anything
other than this entry's own earlier pointer, or appears between planning and writing, the system
SHALL skip the pointer for that entry, still migrate and archive its data, and warn. The entry's own
source path, which the cutover vacates, SHALL NOT count as occupied.

#### Scenario: A real .md sits beside the migrated CSV
- **WHEN** `data/things.csv` is cut over and `data/things.md` already exists with other content
- **THEN** `data/things.md` is unchanged byte-for-byte, and `data/things.csv.pdata-pointer.md`
  exists with the record_group, schema table and example query

#### Scenario: The substitute path is also occupied
- **WHEN** `data/things.csv` is cut over and both `data/things.md` and
  `data/things.csv.pdata-pointer.md` exist and are not this entry's pointer
- **THEN** both are unchanged, no pointer is written for the entry, its data is migrated and
  archived, and a warning names the occupied path

#### Scenario: Entries share a stem with a real .md present
- **WHEN** `data/a.csv` and `data/a.json` are both cut over and a real `data/a.md` exists
- **THEN** `data/a.md` is unchanged and each entry has its own pointer, at
  `data/a.csv.pdata-pointer.md` and `data/a.json.pdata-pointer.md`

#### Scenario: Entries share a stem with no real .md
- **WHEN** `data/a.csv` and `data/a.json` are both cut over and nothing exists at `data/a.md`
- **THEN** the first entry's pointer is written at `data/a.md`, the second entry's at
  `data/a.json.pdata-pointer.md`, and neither overwrites the other

#### Scenario: A non-regular or unreadable occupant
- **WHEN** the primary pointer path is a directory, a symlink (live or dangling), or a file that is
  not valid UTF-8
- **THEN** it is left untouched, the pointer goes to the substitute path, and no error is raised

#### Scenario: The substitute appears after planning
- **WHEN** a file is created at the substitute path after the pointer was planned and before it is
  written
- **THEN** that file is unchanged, the entry's pointer is skipped with a warning, and cutover
  continues

#### Scenario: No collision is unchanged
- **WHEN** nothing exists at the primary pointer path
- **THEN** the pointer is written there exactly as before and no pointer warning is produced

### Requirement: Only an entry's own earlier pointer may be rewritten
The system SHALL rewrite a file at a pointer path only if it is a regular file (not a symlink) whose
first three lines are this entry's pointer heading (`# <original file name> — migrated to pdata`), a
blank line, and a line beginning `This file's data now lives in pdata record group`, tolerating a
leading BOM and CRLF line endings. Any other file SHALL be treated as occupied.

#### Scenario: Re-cutting an entry rewrites its own pointer in place
- **WHEN** the cutover runs for an entry whose primary pointer path already holds that entry's own
  generated pointer
- **THEN** the pointer is rewritten in place and no `.pdata-pointer.md` file is created

#### Scenario: A CRLF or BOM pointer is still recognised
- **WHEN** the existing pointer has CRLF line endings or a leading BOM
- **THEN** it is treated as the entry's own pointer and rewritten in place

#### Scenario: A hand-edited pointer is preserved
- **WHEN** the file at the primary pointer path starts with the entry's heading but its third line
  has been changed
- **THEN** it is left unchanged and the pointer goes to the substitute path

### Requirement: Substituted and skipped pointers are reported
The system SHALL announce each substitution or skip as it happens, list each in the write report
naming the occupied path, and record each in that entry's archive `MANIFEST.md` line. The report
section SHALL be absent when there are none, and a skip SHALL NOT change the command's success exit
status.

#### Scenario: Substitution is visible
- **WHEN** a pointer is written to `data/things.csv.pdata-pointer.md` because `data/things.md` exists
- **THEN** progress output and the write report name `data/things.md` as occupied and the substitute
  as the pointer path, and the entry's `MANIFEST.md` line says the pointer was substituted

#### Scenario: A skip is visible
- **WHEN** an entry's pointer is skipped because both candidate paths are occupied
- **THEN** progress output carries a `WARNING:` line naming the occupied path, the write report lists
  it, the entry's `MANIFEST.md` line says the pointer was skipped, and the exit status is success

### Requirement: `--leave-no-pointer-files` never touches existing files
With `--leave-no-pointer-files`, the system SHALL write no pointer file for any entry and SHALL leave
every existing file at a would-be pointer path unchanged.

#### Scenario: Opt-out beside a real .md
- **WHEN** `--write --leave-no-pointer-files` cuts over `data/things.csv` and `data/things.md` exists
- **THEN** `data/things.md` is unchanged and no pointer file is created
