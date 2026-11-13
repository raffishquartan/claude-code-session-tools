## MODIFIED Requirements

### Requirement: SKILL.md documents the real CLI surface, cache path, and preview limitation
`SKILL.md` SHALL document: the `list`/`remove`/`flip`/`promote` subcommands (not the retired
`--list`/`--remove`/`--flip` flag forms); the cache database's real path,
`~/.local/share/claude/command-cache.db` (overridable via `CCST_CACHE_DB`); that `list` shows a
truncated recovered command preview for each candidate through `ccst telemetry recover`, for
triage only; and that before `promote` the full command SHALL be shown with
`ccst telemetry recover --max-chars 0 <sha>`, any status other than `command` meaning the
plaintext could not be confirmed and the candidate must not be promoted on a guess, and the
recovered text being passed to `promote --preview`.

#### Scenario: A reader follows SKILL.md's usage example verbatim
- **WHEN** a reader copies `SKILL.md`'s documented invocation exactly
- **THEN** the script accepts it without an "unrecognized arguments" or similar CLI error

#### Scenario: A reader looks up the cache file directly
- **WHEN** a reader follows `SKILL.md`'s stated cache database path
- **THEN** that path is the file `hooks/cache.py` actually reads from and writes to

## ADDED Requirements

### Requirement: `list` previews each candidate's command for triage
`list` SHALL print, for each candidate, the recovered command preview (or its `session`/`ambiguous`/`unknown` status) alongside sha, fire count, last-seen and sample session, using the same recovery as `ccst telemetry recover` with one batch pass over the candidates and the default truncation, so a cut preview shows how many characters were elided. `--no-preview` SHALL skip recovery. Invoked with no subcommand the script SHALL behave as `list` with preview.

#### Scenario: Preview shown
- **WHEN** `list` runs with `CCST_FIRES_ACCESS=1` and a candidate's command is recoverable
- **THEN** its line includes that command, single-line, truncated with the elided count when long

#### Scenario: Preview skipped
- **WHEN** `list --no-preview` runs
- **THEN** no transcript is opened and no command text is printed

#### Scenario: No subcommand
- **WHEN** the script runs with no subcommand
- **THEN** it behaves as `list` with preview and does not raise

#### Scenario: Unrecoverable candidate
- **WHEN** a candidate's command cannot be confirmed
- **THEN** its line shows the non-`command` status and the closing note no longer claims that hashes cannot be resolved

#### Scenario: Refusal names the real command
- **WHEN** `list` is run without `CCST_FIRES_ACCESS=1`
- **THEN** the refusal text's re-run command is one that works with the documented invocation
