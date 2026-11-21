# cli/ccd-trusted-dirs Specification

## Purpose

Defines what `ccd` passes to Claude Code about directory access when it launches a new session:
nothing. Claude Code prompts for every untrusted `--add-dir` folder at startup, so access to
sibling projects comes from the user's `permissions.additionalDirectories` instead.

## Requirements

### Requirement: `ccd` passes no `--add-dir` arguments
When `ccd` launches a new Claude Code session, it SHALL NOT pass any `--add-dir` argument to `claude`, regardless
of which session roots are configured. Access to sibling projects is provided by the user's
`permissions.additionalDirectories`, which `ccst doctor` checks (see `cli/doctor-session-root-access`).

#### Scenario: Session roots configured
- **WHEN** `CLAUDE_SESSION_TOOLS_REPO_ROOT` and `CLAUDE_SESSION_TOOLS_PROJ_ROOT` are set and contain project
  directories
- **THEN** the launched `claude` command contains no `--add-dir` argument

#### Scenario: Extra arguments are still forwarded
- **WHEN** the user passes extra arguments after the tag to `ccd`
- **THEN** they appear in the launched `claude` command unchanged, and no `--add-dir` is added alongside them
