# cli/ccd-trusted-dirs Specification

## Purpose

Defines what `ccd` passes to Claude Code about directory access when it launches a new session:
no directory arguments. Claude Code prompts for every untrusted `--add-dir` folder at startup, so
access to sibling projects comes from the user's `permissions.additionalDirectories` instead, and `ccd`
tells the user at launch when a session root is not covered by it.

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

### Requirement: `ccd` hints at launch when a session root is not covered by `additionalDirectories`
When `ccd` is about to launch `claude`, it SHALL print one line to stderr for each configured session root
(`CLAUDE_SESSION_TOOLS_REPO_ROOT`, `CLAUDE_SESSION_TOOLS_PROJ_ROOT`) that is set, exists, and is not covered by the
user's `permissions.additionalDirectories` in `~/.claude/settings.json`, using the same coverage rule as
`ccst doctor`. Each line SHALL name the root and the directory to add. The hint SHALL NOT change the launch: `ccd` still
launches the session and exits with the status it would have had without the hint.

#### Scenario: Uncovered root
- **WHEN** a configured root is not equal to or under any `additionalDirectories` entry, and `ccd` launches a session
- **THEN** stderr contains one hint line naming that root and what to add, printed before `claude` starts

#### Scenario: Every root covered
- **WHEN** every configured root is covered
- **THEN** `ccd` prints no hint

#### Scenario: One of two roots uncovered
- **WHEN** both roots are configured and only one is covered
- **THEN** exactly one hint line is printed, for the uncovered root

#### Scenario: settings.json missing or unreadable
- **WHEN** `~/.claude/settings.json` is missing, is not valid JSON, or is not a JSON object
- **THEN** `ccd` prints no hint and launches normally

#### Scenario: No roots configured
- **WHEN** neither session-root environment variable is set
- **THEN** `ccd` prints no hint and launches normally

#### Scenario: Dry run and failed validation
- **WHEN** `ccd` runs with `--dry-run`, or fails launch validation
- **THEN** no hint is printed beyond the existing dry-run report or validation errors
