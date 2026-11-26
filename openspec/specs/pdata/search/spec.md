# pdata/search Specification

## Purpose
Defines `ccst pdata search`, the read-only verb for finding records by text, regex, field comparisons and date ranges across one or more record groups of a project, with projection, snippets, sorting and counts.

## Requirements

### Requirement: Search is read-only and sees only live records
`ccst pdata search` SHALL NOT write to the project's store, create a store that does not exist, or return soft-deleted records. A project with no store SHALL be rejected with an error naming the project.

#### Scenario: Soft-deleted record is not returned
- **WHEN** a record whose content contains `alice` has been soft-deleted and the search is `--text alice`
- **THEN** that record is not in the output

#### Scenario: Missing store is not created
- **WHEN** project `example` has no store and the search is `--project example --group notes --text x`
- **THEN** the command exits non-zero naming `example`, and no store file exists afterwards

### Requirement: Groups are searched together and named in each row
`--group` SHALL be repeatable; every named group is searched in one call, and every result row SHALL carry its `record_group`. A group that does not exist in the store SHALL be rejected with an error listing the groups that do.

#### Scenario: Multi-group search
- **WHEN** groups `notes` and `letters` each have one record mentioning `alice` and the search is `--group notes --group letters --text alice`
- **THEN** both records are returned, each with its own `record_group`

#### Scenario: Unknown group
- **WHEN** the search names `--group nosuch`
- **THEN** the command exits 2 with an error naming `nosuch` and the existing groups

### Requirement: Text and regex matching
`--text S` SHALL match a record when S occurs as a substring of any searched field; `--regex P` SHALL match when the regular expression P finds a match in any searched field. Both SHALL be case-insensitive unless `--case-sensitive` is given. `--not-text` SHALL invert the predicate, matching records where no searched field matches. The searched fields SHALL default to `content`, `file_path` and every TEXT extension field of the group; `--fields f1,f2` SHALL restrict the search to the named fields. Null fields never match.

#### Scenario: Case-insensitive substring by default
- **WHEN** a record's content is `Met Alice today` and the search is `--text alice`
- **THEN** the record is returned

#### Scenario: Case-sensitive substring
- **WHEN** the same record is searched with `--text alice --case-sensitive`
- **THEN** the record is not returned

#### Scenario: Extension text field searched by default
- **WHEN** a record's TEXT extension field `sender` is `alice@example.com` and its content does not mention alice, and the search is `--text alice`
- **THEN** the record is returned

#### Scenario: --fields restricts the searched fields
- **WHEN** the same record is searched with `--text alice --fields content`
- **THEN** the record is not returned

#### Scenario: Regex
- **WHEN** records have content `invoice 42` and `invoice abc` and the search is `--regex 'invoice \d+'`
- **THEN** only the first record is returned

#### Scenario: Negated text
- **WHEN** records have content `alpha` and `beta` and the search is `--text alpha --not-text`
- **THEN** only the `beta` record is returned

### Requirement: Structured and date filters
Repeatable `--where '<field> <op> <value>'` SHALL behave exactly as in `ccst pdata query`, including stripping one pair of surrounding quotes from the value, and is ANDed with every other predicate. `--since D` and `--until D` SHALL keep records whose `--date-field` falls on or after / on or before the ISO date D (yyyy-MM-dd), inclusive. For a TEXT date field the first ten characters are compared as an ISO date; for `created_at`, `updated_at` and INTEGER or REAL extension fields the value is a Unix epoch compared against the local-time start or end of day D.

#### Scenario: --where reuses query semantics
- **WHEN** records have extension field `status` `open` and `closed` and the search is `--text invoice --where "status = 'open'"`
- **THEN** only the matching open record is returned

#### Scenario: Date range on a TEXT field
- **WHEN** records have `sent_date` `2026-01-05`, `2026-02-10` and `2026-03-15` and the search is `--date-field sent_date --since 2026-02-01 --until 2026-02-28`
- **THEN** only the `2026-02-10` record is returned

#### Scenario: Date range on created_at
- **WHEN** one record was created at midday local time on 2026-01-05 and another on 2026-03-15, and the search is `--date-field created_at --since 2026-03-01`
- **THEN** only the 2026-03-15 record is returned

#### Scenario: Non-ISO date rejected
- **WHEN** the search is `--date-field sent_date --since 05/01/2026`
- **THEN** the command exits 2 with an error saying the date must be yyyy-MM-dd

### Requirement: Output shapes
`--format` SHALL be `json` (default) or `csv`; CSV SHALL use LF line endings and a header row of every output column. Rows SHALL contain the record's base columns, `record_group` and extension fields, like `pdata query`. `--select f1,f2` SHALL limit each row to `id`, `record_group` and the named fields, in that order; `id` and `record_group` are always included. `--sort FIELD` SHALL order rows by that field ascending, or descending with `--desc`, with `id` as a tiebreaker; without `--sort` rows are ordered by group in the order given, then by creation time. `--limit N` SHALL cap the number of rows. `--count-only` SHALL print only the number of matching records: a bare JSON number, or a CSV `count` header and value.

#### Scenario: Projection keeps id and group
- **WHEN** the search is `--text alice --select content`
- **THEN** each row has exactly the keys `id`, `record_group`, `content`

#### Scenario: Sort descending with limit
- **WHEN** three matching records have `updated_at` 100, 200 and 300 and the search is `--sort updated_at --desc --limit 2`
- **THEN** the rows for 300 and 200 are returned in that order

#### Scenario: Count only
- **WHEN** three records match and the search adds `--count-only`
- **THEN** the output is `3`

#### Scenario: CSV uses LF
- **WHEN** the search uses `--format csv`
- **THEN** the output contains no carriage return

### Requirement: Snippets
`--snippet N` SHALL replace each searched field in the output with the first match plus up to N characters either side, marking each cut end with `...`. A searched field with no match SHALL be cut to its first N characters, with `...` when it was longer. Fields that are not searched are output in full.

#### Scenario: Snippet around the first match
- **WHEN** a record's content is `aaaaaaaaaa alice bbbbbbbbbb` and the search is `--text alice --snippet 3 --fields content`
- **THEN** the row's content is `...aa alice bb...`

### Requirement: No match is not an error
A search that matches nothing SHALL exit 0 and print `[]` for JSON, only the header row for CSV, or `0` for `--count-only` (`count` header and `0` for CSV).

#### Scenario: Empty JSON
- **WHEN** nothing matches and the format is json
- **THEN** the output is `[]` and the exit code is 0

#### Scenario: Empty CSV keeps its header
- **WHEN** nothing matches and the format is csv
- **THEN** the output is the header row only and the exit code is 0

### Requirement: Contradictory or empty requests are rejected
The verb SHALL exit 2 with a message naming the problem, before reading any record, when: no predicate is given (none of `--text`, `--regex`, `--where`, `--since`, `--until`); `--text` and `--regex` are both given; the regex does not compile; `--case-sensitive`, `--not-text`, `--fields` or `--snippet` are given without `--text` or `--regex`; `--snippet` is combined with `--not-text` or `--count-only`; `--count-only` is combined with `--select`, `--sort`, `--desc` or `--limit`; `--desc` is given without `--sort`; `--since` or `--until` is given without `--date-field`, or `--date-field` without either; `--since` is later than `--until`; `--limit` or `--snippet` is not a positive integer; or a named field is unknown. Unknown-field errors SHALL list the valid fields. A field named in `--where`, `--sort` or `--date-field` SHALL exist in every searched group; a field named in `--fields` or `--select` SHALL exist in at least one.

#### Scenario: No predicate
- **WHEN** the search is `--project example --group notes` with no other filter
- **THEN** the command exits 2 saying a predicate is required

#### Scenario: Text and regex together
- **WHEN** the search gives both `--text a` and `--regex b`
- **THEN** the command exits 2

#### Scenario: Invalid regex
- **WHEN** the search is `--regex '('`
- **THEN** the command exits 2 naming the regex error

#### Scenario: Unknown field lists valid fields
- **WHEN** the search is `--text a --fields nosuch`
- **THEN** the command exits 2 with an error naming `nosuch` and the group's fields
