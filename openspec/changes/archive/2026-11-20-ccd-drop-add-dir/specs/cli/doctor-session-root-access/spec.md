## ADDED Requirements

### Requirement: `ccst doctor` checks that session roots are covered by `additionalDirectories`
`ccst doctor` SHALL check, for each configured session root (`CLAUDE_SESSION_TOOLS_REPO_ROOT`,
`CLAUDE_SESSION_TOOLS_PROJ_ROOT`) that is set and points at an existing directory, that its resolved path equals or
is a descendant of the resolved path of at least one entry in `permissions.additionalDirectories` of the user's
`~/.claude/settings.json`. Entries are `~`-expanded before comparison. A root that is unset or missing is left to the
existing `ENV:` checks and produces no result from this check.

#### Scenario: Root under a covering entry
- **WHEN** `CLAUDE_SESSION_TOOLS_REPO_ROOT=$HOME/repos` and `additionalDirectories` contains `$HOME`
- **THEN** the check for that root reports OK

#### Scenario: Root equal to an entry
- **WHEN** a root is itself listed in `additionalDirectories`
- **THEN** the check for that root reports OK

#### Scenario: Root resolved through a symlink
- **WHEN** a root is a symlink whose target is under an `additionalDirectories` entry, and the symlink's own path is
  not
- **THEN** the check reports OK, because coverage is judged on resolved paths

#### Scenario: Root not covered
- **WHEN** a configured root is not under any `additionalDirectories` entry, or `additionalDirectories` is absent
- **THEN** the check reports WARN, and the reason names the root and the directory to add to
  `permissions.additionalDirectories` in `~/.claude/settings.json`

#### Scenario: settings.json unreadable
- **WHEN** `settings.json` is missing or is not valid JSON
- **THEN** this check produces no result, because the existing `settings.json` check already reports that
