## ADDED Requirements

### Requirement: The date-field check never changes a write's outcome
The check that produces date-field warnings SHALL NOT make `ccst pdata add` or `update` fail. If
reading the schema for the check errors, no warning SHALL be printed and the command SHALL return
the status the write produced.

#### Scenario: Schema read fails after the write
- **WHEN** a record has been written and reading the schema for the warning check raises a
  database error
- **THEN** no warning is printed and the command exits with the write's own status
