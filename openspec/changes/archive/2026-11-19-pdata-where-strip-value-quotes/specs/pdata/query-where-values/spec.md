## Purpose

Defines how `ccst pdata query --where` interprets the value in a `<field> <op> <value>` clause, so callers can quote values the way they would in a shell or SQL without changing what is matched.

## ADDED Requirements

### Requirement: Surrounding quotes on a --where value are not part of the value
When the value of a `--where` clause begins and ends with the same quote character (single or double), the query SHALL compare against the text between the quotes. A value without surrounding quotes SHALL be compared as written.

#### Scenario: Single-quoted value matches an extension field
- **WHEN** a record has extension field `corr_key` equal to `gmail:abc` and the query is `--where "corr_key = 'gmail:abc'"`
- **THEN** the record is returned

#### Scenario: Double-quoted value matches
- **WHEN** a record has extension field `status` equal to `open` and the query is `--where 'status = "open"'`
- **THEN** the record is returned

#### Scenario: Quoted LIKE pattern
- **WHEN** the query is `--where "corr_key LIKE 'gmail:%'"`
- **THEN** records whose `corr_key` starts with `gmail:` are returned

#### Scenario: Unquoted value is unchanged
- **WHEN** the query is `--where "corr_key = gmail:abc"`
- **THEN** the same record is returned as for the quoted form

#### Scenario: Mismatched quotes are kept
- **WHEN** the value is `'abc"` or has only a leading quote
- **THEN** the value is compared as written, quotes included
