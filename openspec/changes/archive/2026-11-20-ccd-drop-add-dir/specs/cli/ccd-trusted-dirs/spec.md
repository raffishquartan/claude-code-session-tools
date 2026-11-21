## ADDED Requirements

### Requirement: `ccd` passes no `--add-dir` arguments
When `ccd` launches a new Claude Code session, it SHALL NOT pass any `--add-dir` argument to `claude`, regardless
of which session roots are configured. Access to sibling projects is provided by the user's
`permissions.additionalDirectories`, which `ccst doctor` checks (see `cli/doctor-session-root-access`).

#### Scenario: Session roots configured
- **WHEN** `CLAUDE_SESSION_TOOLS_REPO_ROOT` and `CLAUDE_SESSION_TOOLS_PROJ_ROOT` are set and contain project
  directories
- **THEN** the launched `claude` command contains no `--add-dir` argument

#### Scenario: Extra arguments are still forwarded
- **WHEN** the user passes extra arguments after `--` to `ccd`
- **THEN** they appear in the launched `claude` command unchanged, and no `--add-dir` is added alongside them

## REMOVED Requirements

### Requirement: `ccd` trusts every project under a configured session root
**Reason**: Claude Code 2.1.326 and later shows a blocking, never-saved trust prompt for each untrusted `--add-dir`
folder. The access the flags granted is also available from a single ancestor entry in `additionalDirectories`.
**Migration**: Add the home directory (or each session root) to `permissions.additionalDirectories` in
`~/.claude/settings.json`. `ccst doctor` reports a WARN naming the missing entry when this is not done.
