## ADDED Requirements

### Requirement: The dry-run report lists pointer-path collisions
The dry-run classification report SHALL, for each not-yet-migrated `db-owned` entry whose pointer
path is occupied by a file that is not that entry's own earlier pointer, add a warning line naming
the occupied path and stating where `--write` will put the pointer instead (or that it will be
skipped), so the user sees every collision before any write.

#### Scenario: A collision is reported before writing
- **WHEN** the dry run covers a `db-owned` `data/things.csv` and `data/things.md` exists
- **THEN** the report contains a warning naming `data/things.md` as occupied and
  `data/things.pdata-pointer.md` as the pointer path, and no file is modified

#### Scenario: No collision, no warning
- **WHEN** no file occupies any pointer path
- **THEN** the report has no pointer-collision lines
