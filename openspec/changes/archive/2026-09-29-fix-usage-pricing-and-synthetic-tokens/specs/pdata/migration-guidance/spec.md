## ADDED Requirements

### Requirement: `pm-pdata-do-init` warns against mapping a mixed-notation path column to `file_path_column`
`pm-pdata-do-init`'s `SKILL.md` SHALL state, in its `file_path_column` pitfall, that a column
documented to use multiple or shorthand path notations (not just plain paths) is unsafe to map to
`file_path_column` at all, and SHALL direct the reviewer to map it to a plain named TEXT field
instead, as for the subfolder-relative case.

#### Scenario: A reviewer meets a column holding brace-expansion shorthand
- **WHEN** a session reviewing a proposal entry sees a path column whose documentation allows
  shorthand such as brace expansion alongside plain paths
- **THEN** the skill's pitfall directs it to map the column to a plain named TEXT field rather than
  `file_path_column`
