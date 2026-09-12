## Purpose

Defines how `ccst doctor` presents a non-OK check result, so a long free-text reason stays
readable at any terminal width instead of hard-wrapping into what looks like a new, unrelated
line.

## ADDED Requirements

### Requirement: Non-OK results render as a two-line record
`ccst doctor`'s normal output (`format_results`) and its `--drift` report
(`format_drift_report`) SHALL render each WARN/FAIL result as the check's `[STATUS] name` on its
own line, followed by the check's reason as a separate, indented block on the line(s) below —
never concatenated onto the same line as `[STATUS] name`.

#### Scenario: A single WARN result
- **WHEN** `ccst doctor` reports one WARN result
- **THEN** the output's first line is `[WARN] <name>` and the reason text appears indented below
  it, not on the same line

#### Scenario: Multiple results stay visually separated
- **WHEN** `ccst doctor` reports two or more non-OK results
- **THEN** each result's `[STATUS] name` header line is distinguishable from the indented reason
  text of the result before it, regardless of terminal width

### Requirement: The reason block wraps to the detected terminal width
The indented reason block SHALL word-wrap to the width reported by
`shutil.get_terminal_size()`, so no line of the reason exceeds that width, and every wrapped
line carries the same indent as the first.

#### Scenario: Narrow terminal
- **WHEN** the detected terminal width is 80 columns and a reason is longer than fits on one
  indented line at that width
- **THEN** the reason wraps across multiple lines, none exceeding 80 columns, each carrying the
  same leading indent

#### Scenario: Wide terminal
- **WHEN** the detected terminal width is 200 columns
- **THEN** a reason that would wrap at 80 columns instead renders on fewer, longer lines bounded
  by the wider detected width

#### Scenario: Non-tty output falls back to a fixed width
- **WHEN** `ccst doctor`'s output is not connected to a terminal (piped, redirected to a file, or
  produced by the scheduled drift-monitor job) and no `COLUMNS` environment variable is set
- **THEN** the reason wraps at a fixed fallback width of 80 columns

### Requirement: `--all`'s OK-line rows are unaffected
`ccst doctor --all`'s OK results SHALL continue to render as a single-line
`[OK]   name  reason` row, unchanged by this wrapping behavior — only non-OK results use the
two-line record layout.

#### Scenario: OK result stays a single line
- **WHEN** `ccst doctor --all` reports an OK result
- **THEN** that result renders as one line, in the same single-line format as before this change
