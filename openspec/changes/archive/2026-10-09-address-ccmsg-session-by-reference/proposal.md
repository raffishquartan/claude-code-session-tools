## Why

`ccmsg send --to-session` accepts only a full session uuid. A session that knows another session
only by its name (the `cc-sessions/` directory name, for example `20261005-claude-some-tag`) or by
a truncated uuid in a received message's sender field cannot address it. The only fallback,
`--to-description`, is delivered only if some session claims it, so delivery is uncertain. Today
any string is also accepted as a session recipient without checking that such a session exists, so
a mistyped value is stored and never delivered, with no error.

## What Changes

- `ccmsg send --to-session` accepts three forms and always resolves them to the full session uuid
  before storing the message:
  - a full uuid, used as given (unchanged behaviour for the common case);
  - a session name (`cc-sessions/` directory name), looked up in the sessions store;
  - a uuid prefix of at least 8 hex characters, looked up in the sessions store.
- A name or prefix that matches no session, or more than one, is an error (exit 2) that lists the
  candidates (short uuid, name, project directory). Nothing is stored. A value that is none of the
  three forms is an error that names the accepted forms.
- `ccmsg read` prints a `from_uuid:` line with the full sender session uuid, so a recipient can
  reply to a session it knows only from a message.
- The `send-session-message` skill documents the accepted forms.

Not changed: `ccs --json` (its results are per directory and a fork has several uuids, so it
cannot carry one `session_uuid`); the sender-supplied `from` label stays as the sender wrote it.

**Compatibility note:** a non-uuid value that previously passed `--to-session` unchecked (and could
never be delivered) is now rejected unless it resolves. Nothing that was delivered before stops
working.

## Capabilities

### New Capabilities
- `cli/ccmsg-session-reference`: how `ccmsg send --to-session` resolves a session reference, and
  the sender uuid shown by `ccmsg read`.

### Modified Capabilities

## Impact

- `src/cc_session_tools/cli/ccmsg.py` (recipient resolution, `read` output),
  `src/cc_session_tools/lib/sessions_db.py` (uuid-prefix lookup), and a small resolver module under
  `src/cc_session_tools/lib/messaging/`.
- `tests/messaging/test_ccmsg_cli.py` fixtures that pass a placeholder `--to-session` value, plus
  new tests.
- `src/cc_session_tools/skills/send-session-message/SKILL.md`.
- `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (minor bump: additive input forms and output line).
