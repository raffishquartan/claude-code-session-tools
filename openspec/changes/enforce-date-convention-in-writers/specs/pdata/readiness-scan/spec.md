## MODIFIED Requirements

### Requirement: Findings are reported by kind
Findings SHALL be one of these kinds, each with the file and, where it applies, the column:
- Row-level, with a count and up to three example row numbers: `ragged-rows` (field count differs
  from the header), `machine-paths` (a home-directory or drive-user path in a value),
  `null-strings` (a null-like placeholder such as `NO DATA`, `N/A`, `NA`, `NULL`, `NONE`, `TBD`,
  `UNKNOWN`, `-`, `--` or `?`, compared case-insensitively, instead of empty), `duplicate-rows`
  (identical to an earlier row), `comment-rows` (first field begins with `#`), `repeated-header`
  (a data row equal to the header row).
- Column-level, with per-format or per-separator counts and no row numbers: `mixed-date-formats`
  (two or more of ISO date, ISO datetime, dotted `yyyy.MM.dd`, `YYYYMMDD` as a real date, compact
  timestamp `yyyyMMddTHHmm[ss][Z]`, slash-separated, `D Month YYYY`, `Month D, YYYY`),
  `non-iso-dates` (at least one value in a recognised non-ISO date format: dotted, `YYYYMMDD`,
  compact timestamp, slash-separated, `D Month YYYY` or `Month D, YYYY`) and `mixed-separators`
  (both `|` and `;` appear inside values).
- File-level, with no counts: `bad-header` (an empty or duplicate header name), `no-unique-column`
  (at least two data rows and no column that is non-empty and distinct on every row),
  `unreadable`, `bom` (a UTF-8 byte-order mark) and `crlf` (CRLF line endings).
The set of kinds SHALL be defined once in the scanning module so callers and tests enumerate it.

#### Scenario: Mixed date formats
- **WHEN** a column holds both `2026-03-19` and `19/03/2026`
- **THEN** a `mixed-date-formats` finding names that column and the formats seen with counts

#### Scenario: Dotted dates mixed with ISO dates
- **WHEN** a column holds both `2026-03-19` and `2026.03.19`
- **THEN** a `mixed-date-formats` finding names that column and reports the ISO date and dotted
  formats with their counts

#### Scenario: Compact and long-form dates mixed with ISO dates
- **WHEN** a column holds `2026-03-19` together with `20260319`, `19 March 2026` or
  `March 19, 2026`
- **THEN** a `mixed-date-formats` finding names that column and reports each format seen

#### Scenario: Consistent column
- **WHEN** every date in a column uses one format
- **THEN** no `mixed-date-formats` finding is emitted for it

#### Scenario: Uniformly non-ISO column
- **WHEN** every date in a column is dotted `yyyy.MM.dd` (or compact, slash-separated or long form)
- **THEN** a `non-iso-dates` finding names that column and reports the format with its count, and
  no `mixed-date-formats` finding is emitted

#### Scenario: Compact timestamp column
- **WHEN** every value in a column is a compact timestamp such as `20261008T1057`,
  `20261008T105743` or `20261008T105743Z`
- **THEN** a `non-iso-dates` finding names that column and reports the `compact-datetime` format
  with its count, and no `mixed-date-formats` finding is emitted

#### Scenario: Compact timestamps mixed with ISO datetimes
- **WHEN** a column holds both `2026-10-08 10:57` and `20261008T1057`
- **THEN** it has a `mixed-date-formats` finding and a `non-iso-dates` finding, and the
  `non-iso-dates` counts cover only the compact timestamps

#### Scenario: A value that is not a real compact timestamp
- **WHEN** a column holds `20261308T2561` (month 13, minute 61) or `ABC20261008T1057`
- **THEN** the value is not counted as a compact timestamp

#### Scenario: Mixed column reports both kinds
- **WHEN** a column holds both `2026-03-19` and `2026.03.19`
- **THEN** it has a `mixed-date-formats` finding and a `non-iso-dates` finding, and the
  `non-iso-dates` counts cover only the non-ISO formats

#### Scenario: ISO-only column
- **WHEN** every date in a column is an ISO date or ISO datetime
- **THEN** no `non-iso-dates` finding is emitted for it

#### Scenario: Examples never contain cell values
- **WHEN** a `machine-paths` finding is reported
- **THEN** it gives the count and row numbers but none of the matching cell text

#### Scenario: BOM and CRLF
- **WHEN** a CSV starts with a UTF-8 byte-order mark and uses CRLF line endings
- **THEN** it has a `bom` finding and a `crlf` finding, and its header names in the inventory do not
  contain the BOM
