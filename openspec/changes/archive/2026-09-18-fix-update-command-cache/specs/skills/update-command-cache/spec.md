## Purpose

Defines how the `update-command-cache` skill's script is invoked and what its `SKILL.md` may
claim about the tool's CLI surface and behavior, so curating the bash-security-review command
cache doesn't require reverse-engineering a broken import path or a stale command reference.

## ADDED Requirements

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
`~/.local/share/claude/command-cache.db` (overridable via `CCST_CACHE_DB`); and that recovering
a candidate's plaintext command for review is a manual step (walking the named sample session's
transcript), not an automated preview the tool produces.

#### Scenario: A reader follows SKILL.md's usage example verbatim
- **WHEN** a reader copies `SKILL.md`'s documented invocation exactly
- **THEN** the script accepts it without an "unrecognized arguments" or similar CLI error

#### Scenario: A reader looks up the cache file directly
- **WHEN** a reader follows `SKILL.md`'s stated cache database path
- **THEN** that path is the file `hooks/cache.py` actually reads from and writes to
