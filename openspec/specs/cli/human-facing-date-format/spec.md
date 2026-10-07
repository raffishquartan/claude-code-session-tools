# cli/human-facing-date-format Specification

## Purpose
Keeps the dates and times CCST prints for a person in the one ISO text form, while stored and
exchanged machine timestamps stay ISO 8601.

## Requirements

### Requirement: Human-facing dates and times use the ISO text form
A date that a CCST command prints for a human reader, outside a filename and outside a machine
field, SHALL be written `yyyy-MM-dd`. A time of day SHALL be written `yyyy-MM-dd HH:mm`, followed
by `UTC` when the value is in UTC. Such output SHALL NOT use the machine form with `T` and `Z`,
dotted `yyyy.MM.dd`, compact `yyyyMMdd` or a long month name. The text of `ccst doctor` reasons,
the auto-sync failure message, the pdata cutover migration log, the `clean-hook-sessions` table
and the `sent_at` line of `ccmsg read` are covered.

#### Scenario: Doctor reason text
- **WHEN** `ccst doctor` reports a pdata verify run or a failed automatic install sync with a time
- **THEN** the time appears as `yyyy-MM-dd HH:mm UTC`, not as `yyyy-MM-ddTHH:mm:ssZ`

#### Scenario: Cutover migration log line
- **WHEN** a pdata cutover archives a source file and appends its log line
- **THEN** the line begins with the time as `yyyy-MM-dd HH:mm UTC`

#### Scenario: ccmsg read sent time
- **WHEN** `ccmsg read` prints a message
- **THEN** its `sent_at` line shows the time as `yyyy-MM-dd HH:mm UTC`, while the stored value is
  still `yyyy-MM-ddTHH:mm:ssZ`

#### Scenario: clean-hook-sessions table
- **WHEN** `clean-hook-sessions` prints the age or modification time of a session
- **THEN** the time appears as `yyyy-MM-dd HH:mm`

### Requirement: Machine timestamps keep ISO 8601
A timestamp that is stored, compared, parsed back or exchanged with an external system SHALL keep
ISO 8601 with `T` and `Z` or an offset. This covers message, scheduler, telemetry, command-cache
and install-sync stored values, and timestamps read from Claude Code transcripts. Only the
rendering of such a value into prose for a human changes.

#### Scenario: Stored values are unchanged
- **WHEN** a message, a scheduler entry or a telemetry row is written
- **THEN** its timestamp is stored in the same `yyyy-MM-ddTHH:mm:ssZ` form as before this change

#### Scenario: Identifiers are not converted
- **WHEN** a session directory, branch name or backup file is named with a compact date stamp
- **THEN** the stamp format is unchanged
