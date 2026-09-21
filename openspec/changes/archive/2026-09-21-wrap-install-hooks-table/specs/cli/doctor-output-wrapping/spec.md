## MODIFIED Requirements

### Requirement: The reason block wraps to the detected terminal width
The indented reason block SHALL word-wrap to the width reported by
`shutil.get_terminal_size()`, but never less than 40 columns, so no line of the reason exceeds
that width and every wrapped line carries the same indent. A word is never split mid-token: a
path, hyphenated name or backticked command that contains no whitespace stays on one line, and a
single token longer than the available width MAY exceed the width.

#### Scenario: Narrow terminal
- **WHEN** the detected terminal width is 80 columns and a reason is longer than fits on one
  indented line at that width
- **THEN** the reason wraps across multiple lines, none exceeding 80 columns unless it is a single
  unbreakable token, each carrying the same leading indent

#### Scenario: Wide terminal
- **WHEN** the detected terminal width is 200 columns
- **THEN** a reason that would wrap at 80 columns instead renders on fewer, longer lines bounded
  by the wider detected width

#### Scenario: Non-tty output falls back to a fixed width
- **WHEN** `ccst doctor`'s output is not connected to a terminal (piped, redirected to a file, or
  produced by the scheduled drift-monitor job) and no `COLUMNS` environment variable is set
- **THEN** the reason wraps at a fixed fallback width of 80 columns

#### Scenario: Paths and hyphenated names stay intact
- **WHEN** a reason contains a path or hyphenated name that would straddle the wrap column
- **THEN** it moves whole to the next line rather than breaking at a hyphen or mid-word

#### Scenario: Degenerate width
- **WHEN** the detected width is below 40 columns
- **THEN** the effective width is 40 columns
