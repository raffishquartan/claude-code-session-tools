# pdata/readiness-scan Specification

## Purpose
Defines `ccst pdata readiness-scan`, a deterministic scan of a project's CSV files that reports the
mechanical pdata-migration blockers a human or model would otherwise have to find by reading, so
an audit can start from exact facts instead of re-deriving them.

## Requirements

### Requirement: The scan does not touch the project or any pdata store
`ccst pdata readiness-scan --project NAME` SHALL scan `*.csv` files under the project's directory
(`CCST_PROJECTS_ROOT`, default `~/cc`, joined with `NAME`) and SHALL NOT create, modify or delete
any file under that directory (including the directory itself) or any pdata database. It SHALL
exit 0 whenever the scan ran, whether or not it found anything, and exit 2 with a message on
stderr when `NAME` is invalid or its directory does not exist. (Like every `ccst` verb it may pass
through the standard install-sync check; that is outside this requirement.)

#### Scenario: A project with findings
- **WHEN** the scan runs on a project whose CSVs have blockers
- **THEN** it prints the findings and exits 0, and no file under the project changed

#### Scenario: A project directory that does not exist
- **WHEN** `--project` names a directory that does not exist
- **THEN** it exits 2 with a message on stderr and creates nothing

#### Scenario: A project with no CSV files
- **WHEN** the project exists and contains no CSV
- **THEN** it exits 0 and says explicitly that no CSV files were found, so it cannot be mistaken
  for a clean bill of health

### Requirement: Scan coverage equals migration coverage
The scan SHALL skip exactly the directories `ccst pdata init`'s classifier skips
(`init_paths.EXCLUDED_DIR_NAMES`) and no others, and SHALL NOT follow directory symlinks, so that
the set of CSVs it scans equals the set of CSVs `ccst pdata init` would classify. The report SHALL
state how many files it did not scan, broken down by JSON, Markdown and other.

#### Scenario: Same file set as the classifier
- **WHEN** a tree contains CSVs in ordinary directories, a hidden directory such as `.archive/`,
  and inside `.git/` and `cc-sessions/`
- **THEN** the scan covers exactly the CSVs the classifier would classify: the `.archive/` one is
  scanned, the `.git/` and `cc-sessions/` ones are not

#### Scenario: Files not scanned are itemised
- **WHEN** the project also holds JSON and Markdown files
- **THEN** the report gives their counts separately from other files

### Requirement: Each CSV is inventoried
For every scanned CSV the report SHALL give its path relative to the project, its data-row count
(header excluded) and its column count, and, in JSON output, its header names. A "row number" in
any finding means the 1-based data record number with the header excluded, which is not the file's
line number when a quoted cell spans lines; the report SHALL say so.

#### Scenario: Inventory
- **WHEN** a CSV has a header and three records
- **THEN** its inventory shows three data rows and its column count

#### Scenario: Header-only file
- **WHEN** a CSV has only a header
- **THEN** it is inventoried with zero data rows and no unique-column facts or `no-unique-column`
  finding

### Requirement: Unparseable files are reported and do not abort the scan
A file that is empty, not valid UTF-8, not parseable as strict CSV (including an unterminated quote
or a field larger than the CSV field-size limit) SHALL be reported as an `unreadable` finding whose
reason is one of `empty-file`, `not-utf8`, `csv-parse-error` or `field-too-large`, never a raw
exception message, and every other file SHALL still be scanned.

#### Scenario: Not UTF-8
- **WHEN** one CSV is not valid UTF-8
- **THEN** it is reported as `unreadable` with reason `not-utf8` and the others are still scanned

#### Scenario: Unterminated quote
- **WHEN** a CSV ends inside a quoted field
- **THEN** it is reported as `unreadable` with reason `csv-parse-error` rather than as a single
  huge record

### Requirement: Findings are reported by kind
Findings SHALL be one of these kinds, each with the file and, where it applies, the column:
- Row-level, with a count and up to three example row numbers: `ragged-rows` (field count differs
  from the header), `machine-paths` (a home-directory or drive-user path in a value),
  `null-strings` (a null-like placeholder such as `NO DATA`, `N/A`, `NA`, `NULL`, `NONE`, `TBD`,
  `UNKNOWN`, `-`, `--` or `?`, compared case-insensitively, instead of empty), `duplicate-rows`
  (identical to an earlier row), `comment-rows` (first field begins with `#`), `repeated-header`
  (a data row equal to the header row).
- Column-level, with per-format or per-separator counts and no row numbers: `mixed-date-formats`
  (two or more of ISO date, ISO datetime, `YYYYMMDD` as a real date, slash-separated,
  `D Month YYYY`, `Month D, YYYY`) and `mixed-separators` (both `|` and `;` appear inside values).
- File-level, with no counts: `bad-header` (an empty or duplicate header name), `no-unique-column`
  (at least two data rows and no column that is non-empty and distinct on every row),
  `unreadable`, `bom` (a UTF-8 byte-order mark) and `crlf` (CRLF line endings).
The set of kinds SHALL be defined once in the scanning module so callers and tests enumerate it.

#### Scenario: Mixed date formats
- **WHEN** a column holds both `2026-03-19` and `19/03/2026`
- **THEN** a `mixed-date-formats` finding names that column and the formats seen with counts

#### Scenario: Consistent column
- **WHEN** every date in a column uses one format
- **THEN** no `mixed-date-formats` finding is emitted for it

#### Scenario: Examples never contain cell values
- **WHEN** a `machine-paths` finding is reported
- **THEN** it gives the count and row numbers but none of the matching cell text

#### Scenario: BOM and CRLF
- **WHEN** a CSV starts with a UTF-8 byte-order mark and uses CRLF line endings
- **THEN** it has a `bom` finding and a `crlf` finding, and its header names in the inventory do not
  contain the BOM

### Requirement: Unique columns are reported as facts, not recommended keys
For each CSV with at least two data rows the report SHALL list every column that is non-empty and
distinct on every data row as a unique column, labelled `integer-like` when every value is an
integer, and `free-text` when the mean value length exceeds 64 characters. The report SHALL state
that a unique column is not a stable key until a human has confirmed its values are not re-numbered
per session or machine.

#### Scenario: A unique id column
- **WHEN** a file has two or more rows and a column whose values are all present and distinct
- **THEN** that column is listed as unique, and `no-unique-column` is not emitted

#### Scenario: A free-text column that happens to be distinct
- **WHEN** a column's distinct values average more than 64 characters
- **THEN** it is listed with the `free-text` label

### Requirement: Output is Markdown by default and JSON on request, and can be narrowed
`--format markdown` (the default) SHALL emit a compact report that omits full header lists;
`--format json` SHALL emit one JSON object with `files` (inventory, unique columns, header names)
and `findings` arrays. `--path PREFIX` SHALL restrict the scan to CSVs whose project-relative path
starts with `PREFIX`. `--findings-only` SHALL omit the inventory from Markdown output.

#### Scenario: JSON output
- **WHEN** `--format json` is given
- **THEN** stdout parses as one JSON object with `files` and `findings` arrays

#### Scenario: Path restriction
- **WHEN** `--path correspondence/` is given
- **THEN** only CSVs under `correspondence/` are scanned and reported
