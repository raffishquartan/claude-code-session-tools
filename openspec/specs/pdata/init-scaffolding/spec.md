# pdata/init-scaffolding Specification

## Purpose

Defines what starting folder structure, if any, `ccst pdata init` creates for a project, so a
genuinely new project has a visible starting point without imposing structure on an existing or
differently-organized project.

## Requirements

### Requirement: A genuinely new project gets starting subfolders
The first time `ccst pdata init` runs for a project whose root directory does not already exist,
the system SHALL create `correspondence/`, `meetings-and-calls/`, and `workstreams/`
subdirectories under the new project root, whether that first run is a dry-run or `--write` -
`ccst pdata init` already creates the project root and other bookkeeping (its classification
manifest, the project's database) on this same first call, so folder scaffolding is not a new
category of side effect.

#### Scenario: Fresh project gets the starting folders
- **WHEN** `ccst pdata init` is run (dry-run or `--write`) for a project name whose root
  directory does not yet exist
- **THEN** the project root is created along with `correspondence/`, `meetings-and-calls/`, and
  `workstreams/` subdirectories inside it

### Requirement: An existing project's structure is never altered by init
When `ccst pdata init` (dry-run or `--write`) runs against a project whose root directory already
existed before that call, the system SHALL NOT create `correspondence/`, `meetings-and-calls/`,
or `workstreams/` (or any other new subfolder) as a side effect.

#### Scenario: Init against an existing project creates no new folder
- **WHEN** `ccst pdata init` (dry-run or `--write`) is run for a project whose root directory
  already exists
- **THEN** no new subfolder is created, regardless of which folders the project currently has

#### Scenario: Re-running init against an already-initialized project is a no-op for folders
- **WHEN** `ccst pdata init` is run again for a project whose root directory already existed
  before this call (including one scaffolded by a first run, and including one where the user
  has since deleted a scaffolded folder they didn't want)
- **THEN** no new subfolder is created, no existing folder is modified, and a previously-deleted
  scaffolded folder is not recreated

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
