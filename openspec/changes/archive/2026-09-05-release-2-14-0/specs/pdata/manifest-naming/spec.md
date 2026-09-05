## Purpose

Defines the filename and documented permanence of the pdata migration-manifest file, and how the
system resolves it so a rename never breaks an already-migrated project.

## ADDED Requirements

### Requirement: The manifest filename reflects permanent tool state
The pdata migration-manifest file SHALL be named `.pdata-migration-manifest.json`, and every
place that documents it (CLI help text, the `--write` success message, and bundled skill
documentation) SHALL describe it as permanent tool state the project depends on, not a draft or
scratch file.

#### Scenario: A fresh project gets the new manifest name
- **WHEN** `ccst pdata init --write` runs for a project with no existing manifest of either name
- **THEN** the manifest is written as `.pdata-migration-manifest.json`

### Requirement: An existing legacy-named manifest still resolves correctly
The system SHALL resolve a project's manifest through a single function that returns the new
filename if present, else the legacy `.ccst-pdata-proposal.json` filename if that is present
instead, so a project migrated before this rename continues to work without any manual file
rename.

#### Scenario: A project with only the legacy manifest name still verifies and edits correctly
- **WHEN** any pdata command that reads or writes the manifest (`init`, `verify`, `rename-group`,
  or an equivalent) runs against a project whose only manifest file is named
  `.ccst-pdata-proposal.json`
- **THEN** that command reads the existing legacy-named file correctly, exactly as it would the
  new-named file

#### Scenario: A project with both names present prefers the new name
- **WHEN** a project directory contains both `.pdata-migration-manifest.json` and
  `.ccst-pdata-proposal.json`
- **THEN** the system reads and writes `.pdata-migration-manifest.json`, treating the legacy file
  as an inert leftover
