# skills/move-session Specification

## Purpose
Defines what the `move-session` skill writes so a renamed or moved session resumes under its new name, when it refuses to touch a session, and how the skill's script is invoked, so no manual `/rename` step or interpreter guesswork is needed.

## Requirements

### Requirement: A tag-changing operation sets the session's picker label
When the operation changes the session tag (RENAME or MOVE+RENAME) and runs with `--execute`, `move_session.py` SHALL append a `custom-title` record and a matching `agent-name` record, both carrying the new full tag and the session uuid, to the transcript the session resumes into: the transcript resolved for the source session on RENAME, the copied transcript on MOVE+RENAME. A MOVE that does not change the tag SHALL NOT append them, and RENAME-only SHALL NOT append tombstone records.

#### Scenario: RENAME-only
- **WHEN** a session is renamed in place with `--execute`
- **THEN** the original transcript bytes are unchanged as a prefix of the file, exactly two lines follow them (a `custom-title` record whose `customTitle` is the new tag and an `agent-name` record whose `agentName` is the new tag, each with `sessionId` equal to the session uuid), and no tombstone conversation records were added

#### Scenario: MOVE+RENAME
- **WHEN** a session is moved to another project and renamed with `--execute`
- **THEN** the destination transcript ends with those two records and the source transcript receives only the existing tombstone records

#### Scenario: MOVE without rename
- **WHEN** a session is moved to another project with no `--rename-tag`
- **THEN** no `custom-title` or `agent-name` record is appended

#### Scenario: Transcript lacking a trailing newline
- **WHEN** the transcript's last byte is not a newline
- **THEN** the appended records start on their own line and every line of the file still parses as JSON

#### Scenario: Dry run and verify-only
- **WHEN** the script runs without `--execute`, or with `--verify-only`
- **THEN** no transcript is modified

### Requirement: The label is written only when no Claude Code process holds the session
Tag-changing operations SHALL refuse with exit code 2, leaving every transcript unchanged, when the script runs inside the session being renamed, or when any Claude Code process that is still alive records that session uuid, or when the transcript was modified within the last 30 seconds. The refusal SHALL apply whether or not the script itself runs inside Claude Code. The append SHALL also come after destination verification on MOVE+RENAME.

#### Scenario: Run from the live session
- **WHEN** the script is run from inside the session being renamed
- **THEN** it refuses with exit code 2 and the transcript is unchanged

#### Scenario: Session open in another terminal
- **WHEN** the script is run from a plain shell and a live Claude Code process records the session uuid
- **THEN** it refuses with exit code 2 and the transcript is unchanged

#### Scenario: Stale process record
- **WHEN** a process record names the session uuid but its process is not alive and the transcript is idle
- **THEN** the rename proceeds

### Requirement: A failed append is reported, not hidden
After appending, the script SHALL re-read the appended lines and, if they do not parse as the intended records, exit with status 1 naming the transcript.

#### Scenario: Append verified
- **WHEN** the append succeeds
- **THEN** the script's output names the title it wrote

### Requirement: No pending-rename marker or hook
The script SHALL NOT write a `.pending-rename` marker, and CCST SHALL NOT provide a `pending-rename` hook. The `SKILL.md` recipes SHALL NOT tell the user to run `/rename` after a move or rename.

#### Scenario: Marker absent after a rename
- **WHEN** a session is renamed with `--execute`
- **THEN** the destination `cc-sessions/<new-tag>/` contains no `.pending-rename` file

#### Scenario: Hook verb retired
- **WHEN** `ccst hooks run pending-rename` is invoked
- **THEN** it is rejected as an unknown hook, and `ccst hooks install` removes a `settings.json` entry that names it

### Requirement: The script has a supported invocation that works on a standard install
`ccst move-session <args>` SHALL run the bundled script with `<args>` unchanged, including calls whose first argument is an option, and exit with the script's exit code, using an interpreter that has `cc-session-tools` installed. `ccst move-session --help` SHALL print the script's own usage. `SKILL.md`'s quick reference and the in-session refusal recipe SHALL use this form rather than bare `python3 <path>`.

#### Scenario: Forwarded arguments starting with an option
- **WHEN** `ccst move-session --src-session <dir> --rename-tag <tag>` is run
- **THEN** the script prints its dry-run plan for that session and exits 0

#### Scenario: Script help
- **WHEN** `ccst move-session --help` is run
- **THEN** the output is the script's usage, including `--src-session`

#### Scenario: Exit code pass-through
- **WHEN** the script exits non-zero (for example a missing `--src-session`)
- **THEN** `ccst move-session` exits with that same code

#### Scenario: Refusal recipe
- **WHEN** the script refuses an in-session move
- **THEN** the printed re-run command begins `ccst move-session`, and the recipe contains neither `python3 ` nor a `/rename` step
