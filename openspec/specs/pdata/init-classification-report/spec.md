# pdata/init-classification-report Specification

## Purpose

Defines an automatic heuristic in `ccst pdata init`'s dry-run classification report that flags a
`db-owned` entry whose proposed field names look like garbled sentence fragments rather than real
column names, so a reviewer's attention is drawn there without depending on an independent manual
skim catching the same pattern.

## Requirements

### Requirement: The dry-run report flags entries with suspicious field-name shapes
When the dry-run classification report includes a `db-owned` entry with multiple proposed field
names that exceed a length or word-count threshold indicating they read as sentence fragments
rather than short domain nouns, the system SHALL annotate that entry in the printed report as worth
double-checking, distinct from an ordinary `db-owned` entry line.

#### Scenario: A garbled-header entry is flagged
- **WHEN** a dry-run classification report includes a `db-owned` entry whose proposed fields
  include names such as `pending_chris_s_manual_review` (multiple long, many-word field names)
- **THEN** that entry's line in the printed report is annotated as worth double-checking, calling
  out that the file's header may not be what it looks like

#### Scenario: A normal entry is not flagged
- **WHEN** a dry-run classification report includes a `db-owned` entry whose proposed field names
  are short, ordinary column names
- **THEN** that entry's line in the printed report carries no such annotation

### Requirement: The dry-run report lists pointer-path collisions
The dry-run classification report SHALL, for each not-yet-migrated `db-owned` entry whose pointer
path is occupied (including by an earlier entry's pointer in the same run), add a warning line naming
the occupied path and stating where `--write` will put the pointer instead, or that it will be
skipped, and noting that no pointer is written if `--leave-no-pointer-files` is passed. The dry run
SHALL NOT modify any file.

#### Scenario: A collision is reported before writing
- **WHEN** the dry run covers a `db-owned` `data/things.csv` and `data/things.md` exists
- **THEN** the report contains a warning naming `data/things.md` as occupied and
  `data/things.csv.pdata-pointer.md` as the pointer path, and the project tree is identical before
  and after

#### Scenario: A collision between entries is reported
- **WHEN** the dry run covers `data/a.csv` and `data/a.json` and nothing exists at `data/a.md`
- **THEN** the report warns that `data/a.json`'s pointer will go to `data/a.json.pdata-pointer.md`

#### Scenario: Both candidates occupied
- **WHEN** both `data/things.md` and `data/things.csv.pdata-pointer.md` exist
- **THEN** the report says the entry's pointer will be skipped

#### Scenario: No collision, no warning
- **WHEN** no file occupies any pointer path
- **THEN** the report has no pointer-collision lines

#### Scenario: Already-migrated entries are ignored
- **WHEN** an entry already has `migrated_at` set
- **THEN** the report has no pointer line for it
