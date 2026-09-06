## Purpose

Prevents `ccst pdata schema add-field` from silently discarding a type-change request when
re-run against an existing field, which previously left the field on its original type with no
error and no log line.

## ADDED Requirements

### Requirement: Re-running add-field with a different type is rejected, not silently dropped
When `ccst pdata schema add-field` is run against a field name that already exists with a
different `sql_type` than requested, the system SHALL reject the request with a clear,
actionable error rather than silently keeping the field's original type.

#### Scenario: A type-mismatched rerun is rejected
- **WHEN** a field already exists with one SQL type and `add-field` is re-run for that same field
  name with a different SQL type
- **THEN** the command fails with a message naming both the existing type and the requested type,
  and no schema change is made

#### Scenario: A same-type rerun still succeeds (description-only update unaffected)
- **WHEN** a field already exists and `add-field` is re-run for that same field name with the
  same SQL type (optionally updating only the description)
- **THEN** the command succeeds exactly as before this change - only a type change is rejected
