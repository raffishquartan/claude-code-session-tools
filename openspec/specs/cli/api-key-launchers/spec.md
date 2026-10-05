# cli/api-key-launchers Specification

## Purpose
Lets the user start or resume a Claude Code session billed to a chosen Anthropic API key rather
than their subscription, with keys kept in one dedicated file outside the Claude config tree.

## Requirements

### Requirement: `ccdapi` and `ccrapi` launch with an API key
`ccdapi` SHALL behave as `ccd` and `ccrapi` SHALL behave as `ccr` in every respect (arguments,
validation, session directories, environment for hooks) except that the launched `claude`
process receives the selected API key as `ANTHROPIC_API_KEY`, and any ambient credential or
provider switch that would override it (`CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`,
`CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY`) is removed from its
environment. The key value SHALL NOT appear on
the command line, in debug output or in `--dry-run` output.

#### Scenario: New session with a named key
- **WHEN** the user runs `ccdapi -k work my-tag` and the keys file has a `work` entry
- **THEN** `claude` is launched exactly as `ccd my-tag` would launch it, with `ANTHROPIC_API_KEY`
  set to the `work` entry's value in its environment

#### Scenario: Resume with a named key
- **WHEN** the user runs `ccrapi -k work my-tag` and exactly one session matches
- **THEN** `claude` is launched exactly as `ccr my-tag` would launch it, with `ANTHROPIC_API_KEY`
  set to the `work` entry's value

#### Scenario: Dry run hides the key
- **WHEN** the user runs `ccdapi --dry-run -k work my-tag`
- **THEN** the report names the key label `work` and never prints the key value

### Requirement: `-k` selects the key label, with a menu when omitted
`-k <label>` SHALL select the entry with that label. When `-k` is omitted and stdin is a terminal,
the command SHALL print a numbered menu of the file's labels (labels only) and launch with the
one the user picks; cancelling the menu SHALL exit without launching. When `-k` is omitted and
stdin is not a terminal, the command SHALL exit non-zero with an error listing the labels. With
more labels than the picker supports, the command SHALL exit non-zero asking for `-k`.

#### Scenario: Menu pick
- **WHEN** the user runs `ccdapi my-tag` with labels `work` and `personal` in the file, in a terminal
- **THEN** a numbered menu shows `work` and `personal`, and choosing `2` launches with `personal`'s key

#### Scenario: Unknown label
- **WHEN** the user runs `ccdapi -k nope my-tag` and no `nope` entry exists
- **THEN** the command exits non-zero, lists the available labels and does not launch `claude`

#### Scenario: Cancelled menu
- **WHEN** the user presses `q` at the menu
- **THEN** no session is created or resumed

### Requirement: API keys live in a dedicated, unsourced file
Keys SHALL be read from the file named by `CCST_API_KEYS_FILE`, defaulting to
`~/.config/ccst/api-keys`. The format SHALL be one `label=key` entry per line, with blank lines
and `#` comment lines ignored. The file SHALL NOT be located under `~/.claude` and SHALL NOT be
sourced by any shell rc file or fragment that ccst installs. The command SHALL refuse to use the
file, naming the problem without printing any key, when it is missing, has no entries, contains a
malformed or duplicate-label line, or is readable by group or others.

#### Scenario: Missing file
- **WHEN** the keys file does not exist
- **THEN** the command exits non-zero, names the expected path and does not launch `claude`

#### Scenario: Permissive file mode
- **WHEN** the keys file has mode `0644`
- **THEN** the command exits non-zero saying to `chmod 600` the file, and does not launch `claude`

#### Scenario: Comments and blank lines
- **WHEN** the file contains comment lines, blank lines and two `label=key` lines
- **THEN** exactly those two labels are offered

### Requirement: The repository ships a value-free template
The package SHALL include an `api-keys.example` template containing only comments and a
commented-out example entry using an obviously fake placeholder, so that no real key is ever
committed to this repository.

#### Scenario: Template contains no active entries
- **WHEN** the template is parsed with the keys-file parser
- **THEN** it yields zero entries and no error
