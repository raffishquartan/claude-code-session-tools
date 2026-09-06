## Why

Five small, independent, already-fully-scoped items are ready to ship together as one release,
matching the grab-bag pattern 2.12.5 used for four unrelated small items: a notification gap (the
8-digit confirmation gate never reaches the user's phone when it blocks), a display gap (`ccmsg
list` doesn't show message age even though it's already stored), a silent-failure bug
(`add-field` drops a type change with no error), a documentation gap (`add-field` already doubles
as the description-edit command but nowhere says so), and two skill renames for clarity. None of
the five design-couples to the others.

## What Changes

- `confirm_8digit`'s gate calls the existing `send_telegram()` helper (already used for
  scheduled-job failures and pdata sync conflicts) when it blocks or warns, so a push
  notification reaches the user's phone the same way it already does for those two call sites.
  Best-effort - a Telegram failure never affects the gate's terminal behavior.
- `ccmsg list` shows each message's age (using the existing `_relative_age` helper already used
  for the delivery digest) - `MessageRow` gains a `sent_at` field.
- `ccst pdata schema add-field` now rejects (rather than silently no-oping) a rerun against an
  existing field name with a different `sql_type`, with a clear message naming both types and
  what to do instead.
- `README.md` and `ccst pdata schema add-field --help` document that re-running the command
  against an existing field name updates its description in place - no separate
  `edit-description` command is being built.
- Two bundled skills are renamed for clarity: `pm-pdata-audit` -> `pm-pdata-do-audit-and-prepare-to-migrate`,
  `pm-pdata-migrate` -> `pm-pdata-do-migrate`. A new `ccst doctor` check flags a skill symlink left
  behind by a rename (or any other skill removal) so an upgrade doesn't leave a broken symlink
  under `~/.claude/skills/` unnoticed.

## Capabilities

### New Capabilities
- `notify/confirm-8digit-telegram`: the 8-digit confirmation gate pushing a Telegram notification
  when it blocks or warns a gated tool call.
- `cli/ccmsg-message-age`: `ccmsg list` displaying each message's relative age.
- `pdata/add-field-type-mismatch`: `ccst pdata schema add-field` rejecting (not silently
  dropping) a type change on an existing field.
- `cli/skills-stale-symlink-check`: `ccst doctor` flagging a `~/.claude/skills/` symlink that no
  longer corresponds to a bundled skill.

### Modified Capabilities
(none - the `add-field`-doubles-as-description-edit behavior and the two skill renames are
documentation/naming changes with no requirement-level behavior change; nothing in
`openspec/specs/` describes either today.)

## Impact

- **Code**: `src/hooks/confirm_8digit.py`, `src/cc_session_tools/lib/messaging/service.py`,
  `src/cc_session_tools/cli/ccmsg.py`, `src/cc_session_tools/lib/pdata/repository.py`,
  `src/cc_session_tools/lib/pdata/service.py`, `src/cc_session_tools/lib/doctor.py`,
  `src/cc_session_tools/cli/ccst.py` (`--help` text), `README.md`, the two renamed skill
  directories under `src/cc_session_tools/skills/`.
- **Cross-repo**: after the rename, a follow-up `ccmsg` goes to `claude-code-config-sync` (already
  told about the *old* names bundling in via a prior message) with the new names.
- **No on-disk data format changes** - minor version bump (new capabilities/behavior, no breaking
  change), per this repo's own version policy.
