## Why

`ccmsg` help never says what the message statuses mean, so sessions infer the lifecycle from usage and get parts of it wrong (for example, `ccmsg read` does not mark a message read). Inline `--body` text is matched by PreToolUse Bash hooks, so a body that merely mentions a blocked command gets the whole send blocked. And `--to-project` takes only a project name, while senders usually know the recipient as a folder path.

## What Changes

- `ccmsg --help` and `ccmsg list --help` print one shared definition of the statuses `sent`, `read`, `claimed` and `archived`: what moves a message into each, the session-start relisting of read messages, first-claim-wins, the 14-day auto-archive of claimed messages (never of unclaimed ones) and what `ccmsg unarchive` restores.
- `ccmsg send --help` and the `send-session-message` skill recommend `--body-file` (written under the session's `working/` folder) over inline `--body` for long or code-like bodies, and say why.
- `ccmsg send --to-project` also accepts an absolute or `~`-prefixed folder path. The path, or any folder inside it, resolves to the project folder that the sender-side cwd resolution would map a session in that folder to, and the message is stored for that project. A path that belongs to no known project exits non-zero with a clear error. A plain name works as before.
- The `manage-recurring-cc-jobs-using-ccsched` skill notes that `ccsched` has no one-shot form and points to `/schedule` for a single future-dated reminder.

## Capabilities

### New Capabilities
- `cli/ccmsg-status-help`: the help output defines every message status from one source.
- `cli/ccmsg-body-file-guidance`: send help and the send skill recommend `--body-file` for long or code-like bodies.
- `cli/ccmsg-project-path-recipient`: `--to-project` accepts a folder path and resolves it to a project.

### Modified Capabilities

None.

## Impact

- `src/cc_session_tools/cli/ccmsg.py` (help text, recipient resolution), `src/cc_session_tools/lib/messaging/store.py` (path-to-project resolution).
- `src/cc_session_tools/skills/send-session-message/SKILL.md`, `src/cc_session_tools/skills/manage-recurring-cc-jobs-using-ccsched/SKILL.md`, `README.md`.
- Tests under `tests/messaging/`. `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (minor bump to 4.7.0). No on-disk change.
