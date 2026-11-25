## ADDED Requirements

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
When a MOVE is run without `--rename-tag`, without `--force` and without `--no-auto-rename`, and the destination validation fails because the tag suffix does not satisfy the destination strict-root project's `<project>-` prefix rule, the script SHALL derive a new tag and continue as MOVE+RENAME. The derived tag SHALL keep the source `YYYYMMDD-` prefix; its suffix SHALL be the source suffix with one leading `<src-project>-` removed (where `<src-project>` is the source cwd's directory name), prefixed with `<dst-project>-` unless the remainder already starts with `<dst-project>-`. The derived tag SHALL be validated against every destination rule and the clobber check; if it fails any of them, the script SHALL fail with the original validation errors and the derived tag's errors. The script SHALL print `NOTE: tag changed from <old-tag> to <new-tag> (destination project '<p>' requires tag suffix to start with '<p>-')` in the PLAN output (dry-run and execute), in the EXECUTING output, and in the DONE block next to the resume command, together with the picker label the session resumes under.

#### Scenario: Source project prefix is replaced
- **WHEN** session `20260101-oneshot-tidy-garage` in project `oneshot` is moved to strict-root project `home` with no `--rename-tag`
- **THEN** the tag becomes `20260101-home-tidy-garage`, the PLAN shows the NOTE line, and with `--execute` the destination session directory is `cc-sessions/20260101-home-tidy-garage/` and the DONE block repeats the NOTE

#### Scenario: Suffix without a source project prefix
- **WHEN** a session whose suffix does not start with `<src-project>-` is moved to strict-root project `home`
- **THEN** the derived suffix is `home-<original suffix>`

#### Scenario: Similar but distinct prefix is not merged
- **WHEN** session `20260101-oneshot-make-shelf` is moved to strict-root project `making`
- **THEN** the derived tag is `20260101-making-make-shelf`

#### Scenario: Remainder already carries the destination prefix
- **WHEN** session `20260101-oneshot-home-tidy` is moved to strict-root project `home`
- **THEN** the derived tag is `20260101-home-tidy`

#### Scenario: Auto-rename disabled
- **WHEN** the same move is run with `--no-auto-rename`
- **THEN** destination validation fails with the tag-prefix error and nothing is written

#### Scenario: Explicit rename tag is not overridden
- **WHEN** a MOVE+RENAME is run with a `--rename-tag` that fails the strict-root prefix rule
- **THEN** destination validation fails and no tag is derived

#### Scenario: Other validation failures are not masked
- **WHEN** the destination fails a rule other than the tag-prefix rule
- **THEN** the script fails with that error and no tag is derived
