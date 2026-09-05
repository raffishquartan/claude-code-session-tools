## Purpose

Defines what starting folder structure, if any, `ccst pdata init` creates for a project, so a
genuinely new project has a visible starting point without imposing structure on an existing or
differently-organized project.

## ADDED Requirements

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
