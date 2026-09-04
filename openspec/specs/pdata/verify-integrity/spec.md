# pdata/verify-integrity Specification

## Purpose

Defines how `ccst pdata verify` distinguishes a project that was never migrated from one that was
migrated and subsequently lost its migration manifest, so the latter is never silently reported
as healthy.

## Requirements

### Requirement: Verify cross-checks migration-archive evidence independently of the manifest
When a project's migration manifest is absent, `ccst pdata verify` SHALL check for migration
evidence independently of the manifest: whether the project's `.pdata-migrated/` archive
directory exists and has content - the one signal that unambiguously indicates the
classify-and-migrate flow ran, since it is written by that flow alone. If it finds evidence,
verify SHALL report a distinct issue rather than silently treating the project as never migrated.
A project's database having populated record groups is NOT used as an independent signal here:
a project can legitimately accumulate rows entirely through direct record-add operations without
ever running the classify-and-migrate flow or ever having a manifest, so row counts alone cannot
distinguish that from a genuine lost-manifest case.

#### Scenario: Truly never-migrated project reports no manifest-related issue
- **WHEN** `ccst pdata verify` runs against a project with no manifest and no `.pdata-migrated/`
  archive content
- **THEN** verify reports no issue related to the missing manifest - this is a legitimate
  never-migrated project (whether or not its database holds records added directly), not a defect

#### Scenario: Migrated project with a missing manifest is flagged, not silently OK
- **WHEN** `ccst pdata verify` runs against a project with no manifest file, but whose
  `.pdata-migrated/` archive directory has content
- **THEN** verify reports a FAIL-level issue distinct from a normal parity mismatch, whose message
  identifies this as "migrated, manifest now missing" rather than "never migrated"

### Requirement: The manifest-missing issue names concrete recovery steps
The issue raised for a migrated-but-manifest-missing project SHALL name at least one existing
`ccst pdata` command the user can run to inspect the project's current state as part of manual
recovery.

#### Scenario: The issue message points at an existing inspection command
- **WHEN** the manifest-missing-with-evidence issue is raised
- **THEN** its message names `ccst pdata schema show` and/or `ccst pdata schema list` as commands
  to inspect the project's current schema/row-count state while reconciling the missing manifest
