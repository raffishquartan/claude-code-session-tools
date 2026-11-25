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
Tag-changing operations SHALL refuse with exit code 2, leaving every transcript unchanged, when the script runs inside the session being renamed, or when a Claude Code process that is still alive records that session uuid, or when the transcript was modified within the last 30 seconds. A process record SHALL NOT count as a live holder when the live process with that pid started more than two seconds after the start time the record recorded (the pid has been reused); a record with no usable start time, or a process whose start time cannot be read, counts when its pid is alive, and a live process that started at or before the recorded time counts. Every refusal for a process record SHALL name the record file and say that, if no Claude Code session is open, the record is stale and may be deleted. The refusal SHALL apply whether or not the script itself runs inside Claude Code. The append SHALL also come after destination verification on MOVE+RENAME.

#### Scenario: Run from the live session
- **WHEN** the script is run from inside the session being renamed
- **THEN** it refuses with exit code 2 and the transcript is unchanged

#### Scenario: Session open in another terminal
- **WHEN** the script is run from a plain shell and a live Claude Code process records the session uuid
- **THEN** it refuses with exit code 2, the reason names the record file with the stale-record hint, and the transcript is unchanged

#### Scenario: Stale process record
- **WHEN** a process record names the session uuid but its process is not alive and the transcript is idle
- **THEN** the rename proceeds

#### Scenario: Pid reused by another process
- **WHEN** a process record names the session uuid, its pid is alive, but the process now holding that pid started more than two seconds after the record's recorded start time
- **THEN** the record is ignored and the rename proceeds

#### Scenario: Live session, any time zone or locale
- **WHEN** the process holding the pid is the one the record describes, whatever the machine's time zone and locale
- **THEN** the script refuses with exit code 2

#### Scenario: Start time unavailable
- **WHEN** a process record has no start time, or the live process's start time cannot be read, and its pid is alive
- **THEN** the script refuses with exit code 2

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
`ccst move-session <args>` SHALL run the bundled script with `<args>` unchanged, including calls whose first argument is an option, and exit with the script's exit code, using an interpreter that has `cc-session-tools` installed. If the script is killed by a signal, `ccst move-session` SHALL exit with 128 plus the signal number. `ccst move-session --help` SHALL print the script's own usage. `SKILL.md`'s quick reference and the in-session refusal recipe SHALL use this form rather than bare `python3 <path>`.

#### Scenario: Forwarded arguments starting with an option
- **WHEN** `ccst move-session --src-session <dir> --rename-tag <tag>` is run
- **THEN** the script prints its dry-run plan for that session and exits 0

#### Scenario: Script help
- **WHEN** `ccst move-session --help` is run
- **THEN** the output is the script's usage, including `--src-session`

#### Scenario: Exit code pass-through
- **WHEN** the script exits non-zero (for example a missing `--src-session`)
- **THEN** `ccst move-session` exits with that same code

#### Scenario: Killed by a signal
- **WHEN** the script is terminated by a signal
- **THEN** `ccst move-session` exits with 128 plus that signal number

#### Scenario: Refusal recipe
- **WHEN** the script refuses an in-session move
- **THEN** the printed re-run command begins `ccst move-session`, and the recipe contains neither `python3 ` nor a `/rename` step

### Requirement: Verify-only does not count the source's trailing tombstone against it
When `--verify-only` compares line counts, the tombstone records in the source that were appended for the destination being verified (from the tombstone that names that destination to the end of the file) SHALL be excluded from the source's length. If the remaining source length does not exceed the destination's, the line-count check passes and the output SHALL state how many lines were excluded. If the remaining source length still exceeds the destination's, the check fails and the output SHALL say the extra lines are unexplained. Tombstone records for other destinations that are already in the destination copy SHALL NOT be excluded. The other verification checks are unchanged, and strict verification during `--execute` is unchanged.

#### Scenario: Move without rename, tombstone enabled
- **WHEN** `--verify-only` runs after a MOVE that appended tombstone records to the source
- **THEN** the output reports `RESULT : PASS`, exits 0, and names the excluded tombstone lines

#### Scenario: Unexplained extra source lines
- **WHEN** the source has more lines than the destination beyond its trailing tombstone records
- **THEN** the output reports `RESULT : FAIL`, exits non-zero, and says the extra lines are unexplained

#### Scenario: Same source moved twice
- **WHEN** a source was moved to B and then to C, and `--verify-only` is run for the copy at C
- **THEN** only the tombstone naming C is excluded (the one for B is already in the C copy) and the check passes; for the copy at B both tombstones are excluded and the check passes

#### Scenario: Conversation appended after the copy
- **WHEN** the source gained non-tombstone lines after the copy point
- **THEN** those lines are not excluded and the check fails as unexplained

#### Scenario: Destination not shorter
- **WHEN** the source has no more lines than the destination
- **THEN** no explanation is printed

#### Scenario: Lengths already equal
- **WHEN** the source and destination have the same number of lines and the source carries no tombstone for the destination
- **THEN** no exclusion line is printed

### Requirement: Destination cwd values are checked against the rewritten source cwd values
Verification of a destination transcript (strict during `--execute`, and `--verify-only`) SHALL accept every `cwd` value in the destination that equals the destination cwd or equals some `cwd` value found in the source transcript after applying the same path-string replacements the copy applied. Verification SHALL fail when a destination `cwd` value is outside that set, and SHALL list each such value in its output. All other verification checks are unchanged.

#### Scenario: Source session changed directory into its own transcript directory
- **WHEN** a session whose transcript has records with `cwd` equal to the project cwd and other records with `cwd` equal to `~/.claude/projects/<src-key>` is moved with `--execute`
- **THEN** verification reports `RESULT : PASS`, the destination records carry `~/.claude/projects/<dst-key>` in place of the source key path, and the move completes with exit code 0

#### Scenario: Unexpected destination cwd
- **WHEN** a destination transcript contains a `cwd` value that is neither the destination cwd nor a rewritten source `cwd`
- **THEN** verification reports `RESULT : FAIL` and names that value as unexpected

### Requirement: A failed strict verification leaves no partial destination
When strict verification fails during `--execute`, the script SHALL remove every file and directory that run created (the destination session directory, the destination transcript, and any destination parent directory the run created that is now empty), SHALL print each removed path, and SHALL exit with status 1. It SHALL NOT remove any path that existed before the run, SHALL NOT modify the source session directory or source transcript, and SHALL NOT write tombstones, picker labels, `sessions.db` rows or a cleanup script. If a removal fails, the script SHALL print every path it could not remove. Verification SHALL run before any memory directory is copied.

#### Scenario: Failed verification rolls back
- **WHEN** strict verification fails during `--execute`
- **THEN** the exit code is 1, the output lists each removed path, the destination session directory and destination transcript no longer exist, and the source transcript and source session directory are byte-for-byte unchanged

#### Scenario: Re-run after rollback
- **WHEN** the same command is run again after a rolled-back failure
- **THEN** the clobber check does not reject it

#### Scenario: Pre-existing destination project key directory
- **WHEN** the destination project key directory already existed before the failed run
- **THEN** it still exists after the rollback

### Requirement: A move into a strict-root project derives a compliant tag
When a MOVE is run without `--rename-tag`, without `--force` and without `--no-auto-rename`, and the only destination-validation failure is that the tag suffix does not satisfy the destination strict-root project's `<project>-` prefix rule, the script SHALL derive a new tag and continue as MOVE+RENAME. The derived tag SHALL keep the source `YYYYMMDD-` prefix; its suffix SHALL be the source suffix with one leading `<src-project>-` removed (where `<src-project>` is the source cwd's directory name), prefixed with `<dst-project>-` unless the remainder already starts with `<dst-project>-`. A remainder equal to `<dst-project>` gives suffix `<dst-project>`. The derived tag SHALL be validated against every destination rule; if it fails any of them, the script SHALL fail with the original validation errors and the derived tag's errors. The derived tag SHALL then meet the clobber check like any destination tag, aborting with the existing path named. The script SHALL print `NOTE: tag changed from <old-tag> to <new-tag> (destination project '<p>' requires tag suffix to start with '<p>-')` in the PLAN output (dry-run and execute), in the EXECUTING output, and in the DONE block next to the resume command, together with the picker label the session resumes under.

#### Scenario: Source project prefix is replaced
- **WHEN** session `20260101-scratch-tidy-garage` in project `scratch` is moved to strict-root project `garden` with no `--rename-tag`
- **THEN** the tag becomes `20260101-garden-tidy-garage`, the PLAN shows the NOTE line, and with `--execute` the destination session directory is `cc-sessions/20260101-garden-tidy-garage/` and the DONE block repeats the NOTE

#### Scenario: Suffix without a source project prefix
- **WHEN** a session whose suffix does not start with `<src-project>-` is moved to strict-root project `garden`
- **THEN** the derived suffix is `garden-<original suffix>`

#### Scenario: Similar but distinct prefix is not merged
- **WHEN** session `20260101-scratch-build-shelf` is moved to strict-root project `building`
- **THEN** the derived tag is `20260101-building-build-shelf`

#### Scenario: Remainder already carries the destination prefix
- **WHEN** session `20260101-scratch-garden-tidy` is moved to strict-root project `garden`
- **THEN** the derived tag is `20260101-garden-tidy`

#### Scenario: Suffix with no label beyond a project name
- **WHEN** session `20260101-scratch` or `20260101-garden` is moved from `scratch` to strict-root project `garden`
- **THEN** the derived tag is `20260101-garden`, which fails validation, and the script fails with both error sets so the user picks a tag with `--rename-tag`

#### Scenario: Derived tag already taken
- **WHEN** the destination `cc-sessions/<derived-tag>/` already exists
- **THEN** the script aborts naming that path and writes nothing

#### Scenario: Auto-rename disabled
- **WHEN** the same move is run with `--no-auto-rename`
- **THEN** destination validation fails with the tag-prefix error and nothing is written

#### Scenario: Explicit rename tag is not overridden
- **WHEN** a MOVE+RENAME is run with a `--rename-tag` that fails the strict-root prefix rule
- **THEN** destination validation fails and no tag is derived

#### Scenario: Other validation failures are not masked
- **WHEN** the destination fails a rule other than the tag-prefix rule
- **THEN** the script fails with that error, no tag is derived and no derivation is reported
