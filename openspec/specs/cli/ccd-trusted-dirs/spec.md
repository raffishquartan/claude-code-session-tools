# cli/ccd-trusted-dirs Specification

## Purpose

Defines which directories `ccd` tells Claude Code to trust when it launches a new session, so
every project under a configured session root is a usable launch target without per-project
manual configuration.

## Requirements

### Requirement: `ccd` trusts every project under a configured session root
When `ccd` launches a new Claude Code session, it SHALL pass every immediate, non-hidden
subdirectory of each configured session root (`CLAUDE_SESSION_TOOLS_REPO_ROOT`,
`CLAUDE_SESSION_TOOLS_PROJ_ROOT`) to Claude Code as a `--add-dir <path>` argument, so the user
does not have to list each project individually in `settings.json`'s `additionalDirectories`.

#### Scenario: Two roots each containing project directories
- **WHEN** `CLAUDE_SESSION_TOOLS_REPO_ROOT=$HOME/repos` and `CLAUDE_SESSION_TOOLS_PROJ_ROOT=$HOME/cc`
  are both set, and each contains one or more project directories
- **THEN** the launched `claude` command includes one `--add-dir <resolved-path>` pair per
  immediate non-hidden subdirectory of each root, deduplicated by resolved path, with hidden
  subdirectories (names starting with `.`) and non-directory entries excluded

#### Scenario: No session roots configured
- **WHEN** neither `CLAUDE_SESSION_TOOLS_REPO_ROOT` nor `CLAUDE_SESSION_TOOLS_PROJ_ROOT` is set
  (or the configured roots are otherwise invalid)
- **THEN** `ccd` launches the session with no `--add-dir` arguments and does not crash - a missing
  or invalid roots configuration degrades to no extra trust, not an error

#### Scenario: Task-list id lookup also degrades gracefully
- **WHEN** session-roots configuration is invalid or absent
- **THEN** `ccd` still launches successfully with no `CLAUDE_CODE_TASK_LIST_ID` environment
  variable set, rather than crashing while resolving the task-list id for the current project
