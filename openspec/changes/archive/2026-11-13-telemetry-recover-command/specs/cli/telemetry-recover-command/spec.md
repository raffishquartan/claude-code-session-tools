## Purpose
Defines `ccst telemetry recover`, which turns a bash-security-review `input_hash` back into the command that produced it (or at least its session), without storing command text anywhere new and without dumping transcript content.

## ADDED Requirements

### Requirement: Only a command that re-hashes to the input hash is reported
For each requested hash, `ccst telemetry recover` SHALL print the command text only if the SHA-256 of that exact command equals the hash (or begins with the given prefix). A hash may be given in full or as a prefix of at least 8 hex characters, in any letter case, optionally with a `sha256:` prefix. No fuzzy or normalised matching is used.

#### Scenario: Command found
- **WHEN** a transcript of a session that fired hash H contains a Bash command whose SHA-256 is H
- **THEN** the line for H has status `command`, carries the full 64-character hash, and carries that command

#### Scenario: Near-miss command
- **WHEN** the only transcript command differs from the hashed command by whitespace or case
- **THEN** it is not reported as a command for H

#### Scenario: Ambiguous prefix
- **WHEN** a prefix matches more than one distinct full hash
- **THEN** its line has status `ambiguous` and no command is reported

### Requirement: Malformed arguments are rejected before any search
If any argument is shorter than 8 hex characters after normalisation or contains a non-hex character, the command SHALL print a message naming the argument to stderr, exit with status 2, and read neither telemetry nor any transcript. Duplicate arguments SHALL be collapsed to one line.

#### Scenario: Prefix too short
- **WHEN** one of several arguments has fewer than 8 hex characters
- **THEN** the command exits 2 naming that argument and prints no result lines

#### Scenario: Non-hex argument
- **WHEN** an argument contains a character outside 0-9 and a-f
- **THEN** the command exits 2 naming that argument

#### Scenario: Normalisation
- **WHEN** an argument is given as `SHA256:` followed by upper-case hex
- **THEN** it is treated as the same lower-case hash without the prefix

### Requirement: Main and subagent transcripts are searched
For a session S, the transcripts searched SHALL be every `<projects>/<key>/S.jsonl` and every `<projects>/<key>/S/subagents/*.jsonl`.

#### Scenario: Command issued by a subagent
- **WHEN** the matching command exists only in a subagent transcript of the session that fired the hash
- **THEN** the line has status `command` and names that subagent transcript path

### Requirement: A hash that cannot be resolved to a command still points at its session
When no transcript command re-hashes to a hash that telemetry shows fired in a known session, the line SHALL have status `session` and name that session uuid (the most recent firing session when there are several) and, when a transcript file for it exists, its path. When telemetry has no row for the hash and nothing is found, the status SHALL be `unknown`.

#### Scenario: Transcript missing
- **WHEN** hash H fired in session S and none of S's transcripts exist
- **THEN** the line has status `session` and names S

#### Scenario: Hash not in telemetry
- **WHEN** a hash has no telemetry row and `--scan-all` was not given
- **THEN** the line has status `unknown`

#### Scenario: Scan all transcripts
- **WHEN** `--scan-all` is given and a hash with no telemetry row matches a command in any transcript
- **THEN** the line has status `command`

### Requirement: Output is one compact line per hash
The command SHALL print exactly one tab-separated line per distinct valid argument, in argument order: the normalised argument, the status, the full hash (status `command` only, otherwise empty), the session uuid, `<transcript path>:<1-based line number>` where known, and the command. The command SHALL occupy one line: tab, carriage return, line feed and other control characters are escaped, non-ASCII characters stay literal. If the escaped command exceeds `--max-chars` (default 200; `0` for no limit) it SHALL be cut on a character boundary that does not split an escape sequence and suffixed with a marker stating the number of characters cut. No other transcript content SHALL be printed. The exit status SHALL be 0 whenever every line is printed, whatever the statuses.

#### Scenario: Multi-line and tabbed command
- **WHEN** the recovered command contains newlines and tabs
- **THEN** the output for it is still one tab-separated line with those characters escaped

#### Scenario: Truncation
- **WHEN** the command is longer than `--max-chars`
- **THEN** the printed command is cut to that length and ends with a marker giving the number of characters cut

#### Scenario: Unlimited
- **WHEN** `--max-chars 0` is given
- **THEN** the full escaped command is printed with no marker

### Requirement: Batch resolution reads each transcript file once
Resolving several hashes in one invocation SHALL open each transcript file at most once, including when `--scan-all` is combined with hashes that have telemetry rows.

#### Scenario: Two hashes, one session
- **WHEN** two requested hashes both fired in session S
- **THEN** each of S's transcript files is opened once during the invocation

#### Scenario: Mixed backed and unbacked hashes with --scan-all
- **WHEN** `--scan-all` is given with one hash that has a telemetry row and one that does not
- **THEN** every transcript file is opened at most once

### Requirement: Telemetry reads are gated
`ccst telemetry recover` SHALL exit with status 2 and read nothing unless `CCST_FIRES_ACCESS=1` is set in the environment.

#### Scenario: Gate unset
- **WHEN** the command runs without `CCST_FIRES_ACCESS=1`
- **THEN** it prints a refusal naming the variable, exits 2, and opens neither telemetry.db nor any transcript

### Requirement: Recovery writes to no store
Recovering a command SHALL NOT write to telemetry.db or any other store; its output goes to standard output only.

#### Scenario: After a successful recovery
- **WHEN** a command is recovered
- **THEN** telemetry.db's row count and highest id are unchanged
