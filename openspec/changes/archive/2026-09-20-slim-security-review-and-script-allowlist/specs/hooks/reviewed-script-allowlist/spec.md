## Purpose
Skip LLM security review for repeat invocations of scripts that were previously reviewed as safe and
whose content has not changed, regardless of the arguments passed.

## ADDED Requirements

### Requirement: Allowlist is configurable and content-pinned
The hook SHALL consult a user-manageable allowlist of scripts, each identified by resolved path and
the SHA-256 of its content, stored outside the hook source. Users SHALL be able to list, add,
remove and verify entries through `ccst hooks allowlist`.

#### Scenario: manual add records the hash
- **WHEN** the user runs `ccst hooks allowlist add <script>`
- **THEN** an entry is created with the script's resolved path and current content hash, marked as manually added

#### Scenario: verify reports drift
- **WHEN** an allowlisted script's content changed or the file is gone and the user runs `ccst hooks allowlist verify`
- **THEN** each affected entry is reported with its old and current hash (or "missing")

### Requirement: Matching allowlisted invocations skip review
A command that is a single simple invocation of an allowlisted script, with a current content hash
equal to the stored hash, SHALL be allowed without an LLM call whatever its arguments.

#### Scenario: different arguments, same script
- **WHEN** `python3 scripts/log.py add --subject "A"` was allowlisted and `python3 scripts/log.py add --subject "B" --timestamp 2026-09-20` is run from the same project
- **THEN** the hook allows it with no `claude` call and telemetry records a cache hit sourced from the script allowlist

#### Scenario: recognised interpreter forms
- **WHEN** the same script is run as `python3 x.py`, `uv run x.py`, `uv run python3 x.py`, or directly as `./x.py`
- **THEN** each form matches the same entry

#### Scenario: interpreter options prevent a match
- **WHEN** the command is `python3 -c ...`, `python3 -m ...`, `uv run --with pkg x.py`, or has a leading `VAR=value` or `env`
- **THEN** the allowlist does not match and normal tiers apply

#### Scenario: shell composition prevents a match
- **WHEN** the command contains an unquoted pipe, `;`, `&&`, redirection, command substitution, or backticks in addition to an allowlisted script
- **THEN** the allowlist does not match and normal tiers apply

#### Scenario: metacharacters inside quotes do not prevent a match
- **WHEN** an argument is a single-quoted or double-quoted string containing `;` or `|` and no expansion syntax
- **THEN** the allowlist can still match

### Requirement: Heuristic hits are never allowlisted
A command with any Tier 1 heuristic flag SHALL always be escalated for review, whether or not the
script is allowlisted, and SHALL never cause an entry to be added.

#### Scenario: allowlisted script with a flagged argument
- **WHEN** an allowlisted script is invoked with an argument matching a heuristic pattern (for example a `/etc/` path)
- **THEN** the command is reviewed by the LLM

### Requirement: Hash mismatch triggers re-review and is surfaced
If the script's current hash differs from the stored hash, the entry SHALL NOT match, the command
SHALL be reviewed normally, and the user SHALL be told the script changed.

#### Scenario: script edited after being allowlisted
- **WHEN** an allowlisted script's content is changed and it is invoked
- **THEN** stderr states the script changed since allowlisting with truncated old and new hashes, and the command is reviewed

#### Scenario: re-review verdict updates the entry
- **WHEN** that re-review is `safe` and all auto-add conditions hold
- **THEN** the entry's hash is updated
- **WHEN** it is not `safe`
- **THEN** the entry is removed

### Requirement: Automatic additions are conservative
An entry SHALL be added automatically only when all hold: the invocation was reviewed with verdict
`safe`; the review was given the script's content; the script resolves inside the enclosing project
(git root) and no path component is `cc-sessions`; no heuristic flag fired; and the command has a
recognised simple form.

#### Scenario: session scratch script
- **WHEN** a script under `cc-sessions/<session>/working/` is reviewed as safe
- **THEN** no entry is added

#### Scenario: script outside the project
- **WHEN** a script whose realpath is outside the git root (including via a symlink) is reviewed as safe
- **THEN** no entry is added

#### Scenario: script too large to include in review
- **WHEN** the script exceeds the review size bound
- **THEN** it is reviewed as before and no entry is added automatically

### Requirement: Script invocations are reviewed on script content
When the command is a recognised script invocation for a readable script within the size bound, the
review prompt SHALL include the script's content and SHALL ask about behaviour under arbitrary
arguments; the hash stored on auto-add SHALL be that of the same bytes.

#### Scenario: content included and hashed once
- **WHEN** a recognised script invocation is escalated
- **THEN** the prompt contains the script's content and the stored hash equals the SHA-256 of exactly that content

### Requirement: Allowlist use is observable
Allowlist hits and hash-mismatch events SHALL be recorded in hook telemetry/invocations with a
distinguishing source so they can be queried.

#### Scenario: query mismatches
- **WHEN** a hash-mismatch re-review happened
- **THEN** it is identifiable in `hook_invocations` by its `cache_source` value
