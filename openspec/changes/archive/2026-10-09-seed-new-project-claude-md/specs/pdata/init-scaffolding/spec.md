## ADDED Requirements

### Requirement: A genuinely new project gets a starting CLAUDE.md
On the same first run that creates the starter folders, `ccst pdata init` SHALL write
`CLAUDE.md` in the new project root. It SHALL start with a `# <project name>` heading and contain
a `## Date format` section and a `## Line endings` section.

The date section SHALL state: filenames and folder names use `yyyy.MM.dd` (time
`yyyy.MM.dd-HHmm`, partial `yyyy.MM`); a formal external letter uses long form in its header only;
everything else uses `yyyy-MM-dd` with time of day `yyyy-MM-dd HH:mm` and machine timestamps ISO
8601; formats are never mixed inside one column or field; and the exempt categories (identifiers
containing a date, tool-generated names, existing filenames, evidence and documents the project
did not write).

The line-endings section SHALL state: text files use LF, not CRLF; Python's `csv.writer` and
`csv.DictWriter` need `lineterminator='\n'`; text files opened for writing need `newline='\n'`;
a new or rewritten text file is checked to contain no `\r`.

The file SHALL NOT contain a user name, a home-directory path or a link to a file outside the
project.

#### Scenario: Fresh project gets the file
- **WHEN** `ccst pdata init` is run (dry-run or `--write`) for a project whose root directory
  does not yet exist
- **THEN** the new root contains `CLAUDE.md` beginning `# <project name>` with a `## Date format`
  section and a `## Line endings` section, and the file has LF line endings

#### Scenario: Seed text is generic
- **WHEN** the seeded `CLAUDE.md` is read
- **THEN** it contains no home-directory path (`/Users/`, `/home/`, `C:\Users`) and no absolute
  path to any file

### Requirement: An existing project's CLAUDE.md is never created or changed by init
When `ccst pdata init` (dry-run or `--write`) runs against a project whose root directory already
existed before that call, the system SHALL NOT create, overwrite or edit its `CLAUDE.md`.

#### Scenario: Existing project without a CLAUDE.md
- **WHEN** `ccst pdata init` is run for an existing project root that has no `CLAUDE.md`
- **THEN** no `CLAUDE.md` is created

#### Scenario: Existing project with a CLAUDE.md
- **WHEN** `ccst pdata init` is run for an existing project root whose `CLAUDE.md` has other content
- **THEN** that file is byte-for-byte unchanged

#### Scenario: Re-running after the first run
- **WHEN** `ccst pdata init` is run again for a project it scaffolded, and the user has since edited
  or deleted the seeded `CLAUDE.md`
- **THEN** the edit is kept and a deleted file is not recreated
