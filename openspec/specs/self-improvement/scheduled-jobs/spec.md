# self-improvement/scheduled-jobs Specification

## Purpose
Runs the self-improvement loop on every CCST install by bundling its two weekly ccsched jobs, keeps an existing registration of the same job ids under the user's control, and gives the user one command to opt in to the loop.

## Requirements

### Requirement: Two self-improvement jobs are bundled and enabled by default
CCST's bundled ccsched jobs SHALL include:
- `self-improvement-signals`: cadence `every:7d`, coalesce `one`, catch-up window `14d`, timeout `60s`, surfaced, command `ccst self-improvement collect-signals`.
- `self-improvement-auto-review`: cadence `every:7d`, coalesce `one`, catch-up window `14d`, timeout `300s`, surfaced, command `ccst self-improvement auto-review`.

Both SHALL be registered enabled by `ccst ccsched-jobs install --apply`, and their commands SHALL call only CCST.

#### Scenario: Fresh install
- **WHEN** `ccst ccsched-jobs install --apply` runs on a machine where neither id is registered
- **THEN** both jobs are registered and enabled with exactly the definitions above

#### Scenario: Already registered with the bundled definition
- **WHEN** both jobs are already registered with the bundled definitions
- **THEN** the installer reports them as already registered and adds nothing

### Requirement: A registration that runs a replaced bash script is reported, never replaced
When a bundled self-improvement job id is already registered with a command that runs one of the bash scripts the job replaces (`collect-signals.sh` for `self-improvement-signals`, `run-headless-review.sh` for `self-improvement-auto-review`), `ccst ccsched-jobs install` SHALL leave the registration unchanged, SHALL report it as a conflicting definition naming the script and the migration steps (`ccsched remove <id>`, then `ccst ccsched-jobs install --apply`), and SHALL NOT record the id as installed by CCST, so that after `ccsched remove <id>` the next install registers the bundled job without `--reinstall`.

#### Scenario: Bash version registered
- **WHEN** `self-improvement-signals` is registered with the command `bash /some/path/scripts/self-improvement/collect-signals.sh` and `ccst ccsched-jobs install --apply` runs
- **THEN** the registration is unchanged and the output names the job, the script and the two migration commands

#### Scenario: Migrating after removal
- **WHEN** the user removes that registration with `ccsched remove self-improvement-signals` and runs `ccst ccsched-jobs install --apply`
- **THEN** the bundled job is registered

### Requirement: Doctor reports a bash-script registration and an unset-up loop
`ccst doctor`'s check for a bundled job SHALL be WARN, naming the script and the migration steps, when the job id is registered with a command that runs a script it replaces. A `self-improvement:loop` check SHALL be WARN, naming `ccst self-improvement init`, when either self-improvement job is registered and enabled but the loop is not set up (the backlog is missing or has no `## Inbox` heading), and OK otherwise. Neither check SHALL be FAIL.

#### Scenario: Jobs registered, no backlog
- **WHEN** `self-improvement-auto-review` is registered and enabled and `~/.claude/self-improvement-backlog.md` does not exist
- **THEN** `self-improvement:loop` is WARN and names `ccst self-improvement init`

#### Scenario: Loop set up
- **WHEN** the backlog has a `## Inbox` heading
- **THEN** `self-improvement:loop` is OK

#### Scenario: Bash version registered
- **WHEN** `self-improvement-auto-review` is registered with a command running `run-headless-review.sh`
- **THEN** its bundled-job check is WARN and names `ccsched remove self-improvement-auto-review`

### Requirement: `ccst self-improvement init` sets up the loop
`ccst self-improvement init` SHALL create `~/.claude/self-improvement-backlog.md` holding a title, an empty `## Inbox` section and an empty `## Entries` section when no file (or symlink) exists at that path, and exit 0. When the file exists with a `## Inbox` heading it SHALL leave it unchanged, say so and exit 0. When it exists without one it SHALL leave it unchanged, say that a `## Inbox` line must be added, and exit 1. It SHALL never overwrite or truncate an existing file. The jobs and the hook SHALL never create the backlog themselves.

#### Scenario: First run
- **WHEN** no backlog exists and `ccst self-improvement init` runs
- **THEN** the backlog exists with a `## Inbox` heading and the loop counts as set up

#### Scenario: Existing backlog
- **WHEN** the backlog exists with content and a `## Inbox` heading
- **THEN** its bytes are unchanged and the command exits 0

#### Scenario: Existing backlog without an Inbox
- **WHEN** the backlog exists without a `## Inbox` heading
- **THEN** its bytes are unchanged and the command exits 1
