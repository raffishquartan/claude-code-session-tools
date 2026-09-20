# cli/ccusage-dependency Specification

## Purpose
Define how CCST detects, locates and reports the optional `bun` and `ccusage` dependency that
`claude-code-usage reconcile` and authoritative per-block usage analysis rely on.

## Requirements

### Requirement: Doctor reports missing bun and ccusage
`ccst doctor` SHALL include a `deps:bun` check and a `deps:ccusage` check. A missing tool SHALL be
reported as WARN, never FAIL, and the reason SHALL include the command that installs it.

#### Scenario: ccusage and bun both absent
- **WHEN** neither `bun` nor `ccusage` can be resolved
- **THEN** `deps:bun` and `deps:ccusage` are each WARN, and each reason states the install command

#### Scenario: both present and runnable
- **WHEN** `bun --version` and `ccusage --version` both exit 0
- **THEN** both checks are OK and report the version

#### Scenario: checks can be muted independently
- **WHEN** the user runs `ccst doctor --mute deps:ccusage`
- **THEN** `deps:ccusage` no longer appears as un-muted drift and `deps:bun` is unaffected

### Requirement: Doctor distinguishes not-installed from not-on-PATH
When `ccusage` or `bun` exists under bun's global bin directory but is not resolvable via `PATH`, the
corresponding `deps:*` check SHALL be WARN with a reason that names the `PATH` change needed, not the
install command.

#### Scenario: installed but not on PATH
- **WHEN** an executable `ccusage` exists in `$BUN_INSTALL/bin` (default `~/.bun/bin`) and `which ccusage` finds nothing
- **THEN** `deps:ccusage` is WARN and its reason says to add that directory to `PATH`

### Requirement: claude-code-usage locates ccusage without an interactive PATH
`claude-code-usage` SHALL resolve `ccusage` via `PATH` first and then via bun's global bin
directory.

#### Scenario: non-interactive shell without the bun PATH edit
- **WHEN** `ccusage` is installed under `~/.bun/bin`, that directory is not on `PATH`, and `claude-code-usage reconcile` runs
- **THEN** it finds and runs that `ccusage`

#### Scenario: not installed anywhere
- **WHEN** `ccusage` is in neither location
- **THEN** the command fails with a message naming the `analyse-cc-usage` setup step and `ccst doctor`

### Requirement: Skill setup instructions install bun explicitly
The `analyse-cc-usage` skill's setup section SHALL contain a distinct bun-install step with the
concrete command that works on both macOS and Linux/WSL (including the WSL-side-install and `unzip` notes), a `bun --version` verification, a `PATH` instruction covering non-interactive
shells, and a `ccusage --version` verification after `bun add -g ccusage`.

#### Scenario: fresh machine follows the skill
- **WHEN** someone follows the setup section in order on a machine with neither tool
- **THEN** after the last install step both `bun --version` and `ccusage --version` succeed from a new non-interactive shell
