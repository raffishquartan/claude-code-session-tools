## Purpose

Defines what starting folder structure, if any, `ccst pdata init` creates for a project, so a
genuinely new project has a visible starting point without imposing structure on an existing or
differently-organized project.

## ADDED Requirements

### Requirement: A genuinely new project gets starting subfolders
When `ccst pdata init --write` runs against a project whose root directory did not already exist
before that call, the system SHALL create `correspondence/`, `meetings-and-calls/`, and
`workstreams/` subdirectories under the new project root.

#### Scenario: Fresh project gets the starting folders
- **WHEN** `ccst pdata init --write` is run for a project name whose root directory does not yet
  exist
- **THEN** the project root is created along with `correspondence/`, `meetings-and-calls/`, and
  `workstreams/` subdirectories inside it

### Requirement: An existing project's structure is never altered by init
When `ccst pdata init` (dry-run or `--write`) runs against a project whose root directory already
existed before that call, the system SHALL NOT create `correspondence/`, `meetings-and-calls/`,
or `workstreams/` (or any other new subfolder) as a side effect.

#### Scenario: Dry-run against an existing project creates nothing
- **WHEN** `ccst pdata init` is run without `--write` for a project whose root directory already
  exists
- **THEN** no new subfolder is created, regardless of which folders the project currently has

#### Scenario: Re-running init against an already-initialized project is a no-op for folders
- **WHEN** `ccst pdata init --write` is run a second time for a project whose root directory
  already existed before this call (including one already initialized by a first run)
- **THEN** no new subfolder is created and no existing folder is modified or removed
