# skills/update-command-cache Specification

## Purpose
Defines how the `update-command-cache` skill's script is invoked and what its `SKILL.md` may
claim about the tool's CLI surface and behavior, so curating the bash-security-review command
cache doesn't require reverse-engineering a broken import path or a stale command reference.

## Requirements

### Requirement: The script imports its dependencies without a path bootstrap
`update_command_cache.py` SHALL import `hooks.cache` and `cc_session_tools.lib.telemetry_store`
directly, with no `sys.path` manipulation of its own — it relies on being run through an
interpreter that already has `cc-session-tools` installed (matching how the sibling
`move-session` skill's `cc_session_rules.py` imports `cc_session_tools.lib.roots`).

#### Scenario: Run via an interpreter with the package installed
- **WHEN** the script is invoked with an interpreter that has `cc-session-tools` installed
  (`uv run python3 <path>` in a repo checkout, or the venv `uv tool install` creates)
- **THEN** it imports successfully and runs its subcommand with no `ModuleNotFoundError`

#### Scenario: Run via a bare interpreter with the package not installed
- **WHEN** the script is invoked with an interpreter that does not have `cc-session-tools`
  installed
- **THEN** it fails with `ModuleNotFoundError` immediately at the `import hooks.cache` line — a
  clear, expected failure of an unsupported invocation, not a silently wrong path substituted by
  bootstrap logic

### Requirement: The module docstring documents the real subcommands and supported invocation
The module docstring's `Usage:` block SHALL list the script's actual subcommands (`list`,
`remove <sha>`, `flip <sha> <verdict>`, `promote <sha> ...`) and state that it must be run
through an interpreter with `cc-session-tools` installed, not a bare system `python3`.

#### Scenario: Docstring matches argparse
- **WHEN** the module docstring's `Usage:` block is compared against `main()`'s `argparse`
  subparsers
- **THEN** every subcommand and its arguments named in the docstring exists in the subparsers,
  and no subparser is undocumented

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
